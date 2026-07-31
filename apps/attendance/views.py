from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassArm, Subject, Term
from apps.academics.selectors import teaches
from apps.accounts.permissions import RequirePermission
from apps.api.mixins import TenantScopedViewSet
from apps.people.selectors import staff_for, students_visible_to

from . import services
from .models import AttendanceStatus, BiometricDevice, StaffAttendance, StudentAttendance
from .serializers import (
    AttendanceSummarySerializer,
    BiometricEventSerializer,
    BulkAttendanceSerializer,
    StaffAttendanceSerializer,
    StudentAttendanceSerializer,
)

MarkAttendance = RequirePermission("attendance.mark")
ViewAllAttendance = RequirePermission("attendance.view_all")


class StudentAttendanceViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    serializer_class = StudentAttendanceSerializer
    # `subject__isnull` is how a client asks for the daily register alone. The
    # daily and per-period records for one pupil on one day are separate rows
    # by design, and a register screen that reads both shows — and versions —
    # the wrong one.
    filterset_fields = {
        "term": ["exact"],
        "date": ["exact", "gte", "lte"],
        "class_arm": ["exact"],
        "subject": ["exact", "isnull"],
        "status": ["exact"],
        "student": ["exact"],
    }
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return StudentAttendance.objects.filter(
            student__in=students_visible_to(self.request.user)
        ).select_related("student", "subject", "class_arm")

    @extend_schema(
        request=BulkAttendanceSerializer,
        responses={200: None, 422: None},
        description="Mark a whole class in one transactional request. Rows carry the "
        "version the device last saw; a mismatch is reported as a per-row CONFLICT "
        "instead of silently overwriting.",
    )
    @action(detail=False, methods=["post"], permission_classes=[MarkAttendance])
    def bulk(self, request):
        serializer = BulkAttendanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        term = Term.objects.filter(pk=data["term"]).first()
        if term is None:
            return Response(
                {"error": {"code": "TERM_NOT_FOUND", "message": "Unknown term.", "details": {}}},
                status=404,
            )
        class_arm = (
            ClassArm.objects.filter(pk=data["class_arm"]).first() if data.get("class_arm") else None
        )
        subject = (
            Subject.objects.filter(pk=data["subject"]).first() if data.get("subject") else None
        )

        staff = staff_for(request.user)
        unscoped = request.user.has_perm_code("attendance.view_all")
        if (
            subject is not None
            and not unscoped
            and not teaches(staff, subject=subject, class_arm=class_arm, term=term)
        ):
            return Response(
                {
                    "error": {
                        "code": "NOT_YOUR_CLASS",
                        "message": "You are not assigned to teach this.",
                        "details": {},
                    }
                },
                status=403,
            )

        allowed = {str(pk) for pk in students_visible_to(request.user).values_list("pk", flat=True)}
        rows = [
            services.AttendanceRow(
                student_id=str(row["student"]),
                status=row["status"],
                note=row.get("note", ""),
                version=row.get("version"),
                recorded_at=row.get("recorded_at"),
            )
            for row in data["rows"]
        ]

        outcome = services.mark_bulk(
            term=term,
            date=data["date"],
            rows=rows,
            subject=subject,
            class_arm=class_arm,
            marked_by=staff,
            allowed_student_ids=allowed,
        )
        return outcome.as_response()

    @extend_schema(responses={200: AttendanceSummarySerializer(many=True)})
    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Per-student totals for a term — the figure that goes on a report card."""
        queryset = self.filter_queryset(self.get_queryset())
        rows = (
            queryset.values("student", "student__first_name", "student__last_name")
            .annotate(
                present=Count("pk", filter=Q(status=AttendanceStatus.PRESENT)),
                absent=Count("pk", filter=Q(status=AttendanceStatus.ABSENT)),
                late=Count("pk", filter=Q(status=AttendanceStatus.LATE)),
                excused=Count("pk", filter=Q(status=AttendanceStatus.EXCUSED)),
                total=Count("pk"),
            )
            .order_by("student__last_name")
        )
        payload = [
            {
                "student": row["student"],
                "full_name": f"{row['student__first_name']} {row['student__last_name']}".strip(),
                "present": row["present"],
                "absent": row["absent"],
                "late": row["late"],
                "excused": row["excused"],
                "total": row["total"],
                # Late still counts as attendance; absent and excused do not.
                "percentage": (
                    round(100 * (row["present"] + row["late"]) / row["total"], 1)
                    if row["total"]
                    else 0.0
                ),
            }
            for row in rows
        ]
        return Response(payload)


class StaffAttendanceViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    queryset = StaffAttendance.objects.select_related("staff")
    serializer_class = StaffAttendanceSerializer
    filterset_fields = ["staff", "date", "status"]

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [ViewAllAttendance()]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.has_perm_code("attendance.view_all"):
            return queryset
        return queryset.filter(staff__user=self.request.user)


class BiometricWebhookView(APIView):
    """Gate terminals post punches here.

    Unauthenticated by design — a fingerprint reader holds no session — so
    every request must carry an HMAC signature over the raw body, keyed by
    that device's secret.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(request=BiometricEventSerializer, responses={202: None})
    def post(self, request, device_code: str):
        device = BiometricDevice.objects.filter(code=device_code, is_active=True).first()
        signature = request.headers.get("X-Device-Signature", "")
        if device is None or not services.verify_device_signature(device, request.body, signature):
            # Same answer for unknown device and bad signature: no probing.
            return Response(
                {
                    "error": {
                        "code": "DEVICE_UNAUTHORISED",
                        "message": "Device not recognised.",
                        "details": {},
                    }
                },
                status=401,
            )

        serializer = BiometricEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = services.ingest_biometric_event(device=device, **serializer.validated_data)
        return Response({"accepted": True, "matched": event.processed_at is not None}, status=202)
