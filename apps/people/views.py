from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.academics.models import ClassArm, ClassLevel, Programme
from apps.accounts.permissions import RequirePermission
from apps.api.mixins import TenantScopedViewSet, lookup_param
from apps.assessment import pdf
from apps.audit.models import AuditAction
from apps.audit.services import record
from apps.common import verification
from apps.tenants.db import tenant_atomic

from . import imports, selectors, services
from .models import (
    Application,
    Guardian,
    Staff,
    StudentDocument,
    StudentGuardian,
    StudentStatus,
)
from .serializers import (
    ApplicationSerializer,
    ApplicationTransitionSerializer,
    ConvertApplicationSerializer,
    GuardianSerializer,
    LinkGuardianSerializer,
    StaffAccountSerializer,
    StaffListSerializer,
    StaffSerializer,
    StudentDocumentSerializer,
    StudentImportSerializer,
    StudentListSerializer,
    StudentSerializer,
)

#: A card is only worth printing for a whole class, and 400 pages is already a
#: long print job. Beyond that the office filters again rather than the server
#: building a hundred-megabyte PDF nobody waits for.
ID_CARD_BATCH_LIMIT = 400

ManageStudent = RequirePermission("people.manage_student")
ManageStaff = RequirePermission("people.manage_staff")
ManageUsers = RequirePermission("admin.manage_users")
ReviewAdmissions = RequirePermission("admissions.review")


def _id_card_pdf(request, students, *, filename: str):
    """One PDF, one card per page, each carrying its own verification QR.

    The QR is signed per student, so a batch is not a batch of interchangeable
    cards: swapping the photograph on one does not make its code check out.
    """
    from apps.academics.models import AcademicSession

    tenant = getattr(request, "tenant", None)
    session = AcademicSession.objects.filter(is_current=True).first()
    cards = [
        {
            "student": student,
            "photo": pdf.image_path(student.photo),
            "class_name": str(student.current_arm or student.current_level or ""),
            "programme": str(student.programme or ""),
            "session": session.name if session else "",
            "valid_to": session.end_date if session else None,
            "verification": verification.stamp(
                verification.ID_CARD, student.pk, tenant=tenant, scale=2
            ),
        }
        for student in students
    ]
    return pdf.pdf_response(
        "people/id_card.html",
        {"cards": cards, "branding": pdf.branding(tenant)},
        filename=filename,
    )


class StudentViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    """Student records.

    The queryset is scoped, not just the object permission: a teacher's list
    contains only their own pupils, so pagination and counts leak nothing
    either.
    """

    serializer_class = StudentSerializer
    filterset_fields = ["status", "current_level", "current_arm", "programme", "stream", "gender"]
    search_fields = ["first_name", "last_name", "admission_number", "matriculation_number"]

    def get_queryset(self):
        return selectors.students_visible_to(self.request.user).select_related(
            "current_level", "current_arm", "programme"
        )

    def get_serializer_class(self):
        return StudentListSerializer if self.action == "list" else StudentSerializer

    def get_permissions(self):
        if self.action in {"list", "retrieve", "mine"}:
            return [IsAuthenticated()]
        return [ManageStudent()]

    def perform_create(self, serializer):
        """Create the record and put the pupil on the roll in one act.

        The CSV import and the admissions conversion both follow
        `create_student` with `enrol_student`; the plain POST behind the
        office's "Add student" form did not. That left a hand-entered pupil
        with a `current_arm` and no `Enrolment` — and an ACTIVE enrolment is
        what the class list, the promotion engine, the capacity count and next
        term's invoice run all read as "on the roll". The child sat in the
        class and appeared on none of them.

        One transaction, so a full class is a refusal rather than a stray
        record the registrar has to spot and delete before trying again.
        """
        from apps.academics.models import AcademicSession
        from apps.academics.services import enrol_student

        with tenant_atomic():
            student = services.create_student(
                tenant=getattr(self.request, "tenant", None), **serializer.validated_data
            )
            session = AcademicSession.objects.filter(is_current=True).first()
            # ADMITTED is "admitted, not yet enrolled": an offer taken up is
            # not yet a seat filled, and enrolling one here would put them on
            # the class list and on the invoice run before they turn up. A
            # record with no level has no cohort to be placed in at all.
            if (
                session is not None
                and student.current_level is not None
                and student.status == StudentStatus.ACTIVE
            ):
                enrol_student(
                    student=student,
                    session=session,
                    level=student.current_level,
                    class_arm=student.current_arm,
                    programme=student.programme,
                )
        serializer.instance = student

    def perform_update(self, serializer):
        """Status goes through the service; everything else is a plain edit.

        The same shape as `StaffViewSet.perform_update`, and for the same
        reason one step along: writing `status: TRANSFERRED` from the ordinary
        edit form has to close the enrolment too, or the one field that means
        "this pupil has left" leaves them on the class list and on next term's
        invoice run.
        """
        student = serializer.instance
        before = student.status
        after = serializer.validated_data.get("status", before)
        serializer.save()
        if after != before:
            services.set_student_status(serializer.instance, after, previous=before)

    @extend_schema(request=LinkGuardianSerializer, responses={201: GuardianSerializer})
    @action(detail=True, methods=["post"], permission_classes=[ManageStudent])
    def guardians(self, request, pk=None):
        student = self.get_object()
        serializer = LinkGuardianSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = services.link_guardian(student=student, **serializer.validated_data)
        return Response(GuardianSerializer(link.guardian).data, status=201)

    @extend_schema(
        # `link_id` is a StudentGuardian, not a field on Student, so the
        # generator cannot infer it and downgrades the whole schema build.
        parameters=[
            OpenApiParameter(
                "link_id",
                OpenApiTypes.UUID,
                OpenApiParameter.PATH,
                description="The student–guardian link",
            )
        ],
        responses={204: None},
    )
    @action(
        detail=True,
        methods=["delete"],
        url_path="guardians/(?P<link_id>[^/.]+)",
        permission_classes=[ManageStudent],
    )
    def unlink_guardian(self, request, pk=None, link_id=None):
        """Detach a parent from a child.

        A hard delete, deliberately: the link is what lets a guardian read this
        child's results and invoices, and a school that removes it means it
        gone rather than hidden. The audit row is what survives.
        """
        student = self.get_object()
        link = get_object_or_404(StudentGuardian, pk=link_id, student=student)
        guardian_name = link.guardian.full_name
        link.delete()
        record(
            AuditAction.GUARDIAN_UNLINKED,
            request=request,
            obj=student,
            summary=f"{guardian_name} detached from {student.full_name}",
        )
        return Response(status=204)

    @extend_schema(responses={200: StudentSerializer})
    @action(detail=True, methods=["post"], permission_classes=[ManageStudent])
    def matriculate(self, request, pk=None):
        student = self.get_object()
        services.assign_matriculation_number(student, tenant=getattr(request, "tenant", None))
        record(
            AuditAction.ROLE_ASSIGNED,
            request=request,
            obj=student,
            summary=f"Matriculation number issued: {student.matriculation_number}",
        )
        return Response(StudentSerializer(student).data)

    @extend_schema(responses={200: StudentListSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def mine(self, request):
        """A guardian's wards, in one call for the home screen."""
        wards = selectors.wards_of(request.user)
        return Response(StudentListSerializer(wards, many=True).data)

    @extend_schema(
        request=StudentImportSerializer,
        responses={200: None, 422: None},
        description=(
            "Create students from a CSV. One transaction: a file with a bad row "
            "writes nothing and every error names its row index."
        ),
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="import",
        permission_classes=[ManageStudent],
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_csv(self, request):
        from apps.academics.models import AcademicSession

        serializer = StudentImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        session = (
            AcademicSession.objects.filter(pk=data["session"]).first()
            if data.get("session")
            else None
        )
        outcome = imports.import_students(
            data["file"],
            session=session,
            tenant=getattr(request, "tenant", None),
            enforce_capacity=data["enforce_capacity"],
        )
        if outcome.created:
            record(
                AuditAction.STUDENTS_IMPORTED,
                request=request,
                summary=f"Imported {outcome.created} student record(s) from CSV",
            )
        return outcome.as_response()

    @extend_schema(responses={200: None}, description="This student's ID card, as a PDF.")
    @action(
        detail=True,
        methods=["get"],
        url_path="id-card",
        permission_classes=[ManageStudent],
        # Content negotiation runs before dispatch: without the PDF renderer
        # declared, a client sending `Accept: application/pdf` is answered 406
        # and never reaches this action.
        renderer_classes=pdf.PDF_RENDERERS,
    )
    def id_card(self, request, pk=None):
        student = self.get_object()
        return _id_card_pdf(request, [student], filename=f"id-{student.display_number}.pdf")

    @extend_schema(
        responses={200: None},
        description="ID cards for a whole class, one card per page, in one PDF.",
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="id-cards",
        permission_classes=[ManageStudent],
        renderer_classes=pdf.PDF_RENDERERS,
    )
    def id_cards(self, request):
        """Because nobody prints one card. An office prints a class."""
        students = self.get_queryset()
        for field, model in (
            ("current_arm", ClassArm),
            ("current_level", ClassLevel),
            ("programme", Programme),
        ):
            record = lookup_param(request, field, model)
            if record is not None:
                students = students.filter(**{field: record})
        return _id_card_pdf(request, list(students[:ID_CARD_BATCH_LIMIT]), filename="id-cards.pdf")


class GuardianViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    queryset = Guardian.objects.all()
    serializer_class = GuardianSerializer
    search_fields = ["first_name", "last_name", "phone"]
    permission_classes = [RequirePermission("people.manage_guardian")]


class StaffViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    """Personnel records, and the login that hangs off one.

    Two permissions, deliberately: `people.manage_staff` edits the personnel
    file, `admin.manage_users` decides who can sign in and with what powers. A
    bursar keeping HR records straight is not thereby able to make themselves
    principal.
    """

    queryset = Staff.objects.alive().select_related("department", "user")
    serializer_class = StaffSerializer
    filterset_fields = ["staff_type", "department", "status"]
    search_fields = ["first_name", "last_name", "staff_number"]

    def get_serializer_class(self):
        return StaffListSerializer if self.action == "list" else StaffSerializer

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        if self.action in {"list", "retrieve"}:
            return [RequirePermission("people.view_staff")()]
        if self.action == "account":
            return [ManageUsers()]
        return [ManageStaff()]

    def perform_create(self, serializer):
        staff = services.create_staff(
            tenant=getattr(self.request, "tenant", None), **serializer.validated_data
        )
        serializer.instance = staff

    def perform_update(self, serializer):
        """Status goes through the service; everything else is a plain edit.

        Writing `status: EXITED` from the ordinary edit form has to disable the
        login too, or the one field that means "this person no longer works
        here" is the one field that does nothing.
        """
        staff = serializer.instance
        before = staff.status
        after = serializer.validated_data.get("status", before)
        serializer.save()
        if after != before:
            services.set_staff_status(serializer.instance, after, previous=before)
            record(
                AuditAction.ROLE_ASSIGNED,
                request=self.request,
                obj=serializer.instance,
                summary=f"{serializer.instance.full_name}: staff status {before} to {after}",
                before={"status": before},
                after={"status": after},
            )

    @extend_schema(request=StaffAccountSerializer, responses={200: StaffSerializer})
    @action(detail=True, methods=["post"])
    def account(self, request, pk=None):
        """Give this staff member a login on their own phone number.

        No credential is issued: they sign in with an OTP to that number and
        set their own PIN. Re-posting is how roles are changed later, and is
        safe — the account is matched, not recreated.
        """
        staff = self.get_object()
        serializer = StaffAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.link_staff_account(
            staff, roles=serializer.validated_data["roles"], request=request
        )
        staff.refresh_from_db()
        return Response(StaffSerializer(staff).data)

    @extend_schema(responses={200: StaffSerializer})
    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def me(self, request):
        staff = selectors.staff_for(request.user)
        if staff is None:
            return Response(
                {
                    "error": {
                        "code": "NOT_STAFF",
                        "message": "This account has no staff record.",
                        "details": {},
                    }
                },
                status=404,
            )
        return Response(StaffSerializer(staff).data)


class ApplicationViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    queryset = Application.objects.select_related("session", "level_applied", "programme_applied")
    serializer_class = ApplicationSerializer
    filterset_fields = ["status", "session", "level_applied", "programme_applied"]
    search_fields = ["reference", "first_name", "last_name"]
    permission_classes = [ReviewAdmissions]

    def perform_create(self, serializer):
        serializer.instance = services.submit_application(**serializer.validated_data)

    @extend_schema(request=ApplicationTransitionSerializer, responses={200: ApplicationSerializer})
    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        application = self.get_object()
        serializer = ApplicationTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        before = application.status
        services.transition_application(
            application,
            data["status"],
            staff=selectors.staff_for(request.user),
            notes=data.get("notes", ""),
            score=data.get("score"),
        )
        record(
            AuditAction.ROLE_ASSIGNED,
            request=request,
            obj=application,
            summary=f"Application {application.reference}: {before} to {application.status}",
            before={"status": before},
            after={"status": application.status},
        )
        return Response(self.get_serializer(application).data)

    @extend_schema(request=ConvertApplicationSerializer, responses={201: StudentSerializer})
    @action(detail=True, methods=["post"])
    def enrol(self, request, pk=None):
        """Accepted offer becomes a student record, a guardian link and an enrolment."""
        from apps.academics.models import AcademicSession, ClassArm

        application = self.get_object()
        serializer = ConvertApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        session = (
            AcademicSession.objects.filter(pk=data["session"]).first()
            if data.get("session")
            else AcademicSession.objects.filter(is_current=True).first()
        )
        class_arm = (
            ClassArm.objects.filter(pk=data["class_arm"]).first() if data.get("class_arm") else None
        )

        student = services.convert_application_to_student(
            application,
            session=session,
            class_arm=class_arm,
            tenant=getattr(request, "tenant", None),
        )
        return Response(StudentSerializer(student).data, status=201)


class StudentDocumentViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    queryset = StudentDocument.objects.select_related("student")
    serializer_class = StudentDocumentSerializer
    filterset_fields = ["student"]
    permission_classes = [ManageStudent]

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(student__in=selectors.students_visible_to(self.request.user))
        )

    def perform_create(self, serializer):
        serializer.save(uploaded_by=selectors.staff_for(self.request.user))
