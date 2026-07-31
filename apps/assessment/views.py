"""Assessment endpoints.

Two audiences with opposite needs share this module: a teacher on a phone who
wants one mark sheet and one bulk write, and an exams officer on a laptop who
wants to compute, publish and print a whole cohort. Both go through the same
services, so a mark entered on the phone and a mark entered in the office are
the same mark.
"""

from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.academics.models import ClassArm, ClassLevel, Subject, Term
from apps.academics.selectors import teaches
from apps.accounts.permissions import RequirePermission
from apps.api.deletion import ProtectDependentsMixin
from apps.api.exceptions import error_payload
from apps.api.mixins import TenantScopedViewSet, lookup_param
from apps.common import verification
from apps.people.models import Student
from apps.people.selectors import can_view_student, staff_for, students_visible_to

from . import pdf, selectors, services
from .models import AssessmentComponent, GradingScale, Score, SubjectResult, TermResult
from .serializers import (
    AssessmentComponentSerializer,
    BulkScoreSerializer,
    ComputeTermSerializer,
    GradingScaleSerializer,
    PromotionApplySerializer,
    PromotionOverrideSerializer,
    PromotionRunSerializer,
    PublishTermSerializer,
    ScoreSerializer,
    SubjectResultSerializer,
    TermResultSerializer,
)

EnterScore = RequirePermission("assessment.enter_score")
ViewAllScores = RequirePermission("assessment.view_all_scores")
PublishResults = RequirePermission("assessment.publish_results")
GenerateReport = RequirePermission("assessment.generate_report")
OverridePromotion = RequirePermission("assessment.override_promotion")
ManageStructure = RequirePermission("academics.manage_structure")


class AssessmentComponentViewSet(
    ProtectDependentsMixin, TenantScopedViewSet, viewsets.ModelViewSet
):
    """Mark-sheet columns. Everyone reads them; only an admin changes them.

    `Score.component` is a CASCADE, so deleting the "Exam" column deleted
    every exam mark entered under it. `is_active` is how a column stops being
    offered — it is already respected by `components_for`.
    """

    queryset = AssessmentComponent.objects.select_related("level", "subject")
    serializer_class = AssessmentComponentSerializer
    filterset_fields = ["level", "subject", "is_active"]

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [ManageStructure()]


class GradingScaleViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    queryset = GradingScale.objects.prefetch_related("grade_bands").select_related("level")
    serializer_class = GradingScaleSerializer
    filterset_fields = ["level", "is_default"]

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [ManageStructure()]


class ScoreViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    """Raw marks. Written through `bulk`; the mark sheet is read through `sheet`."""

    serializer_class = ScoreSerializer
    filterset_fields = ["registration", "component"]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return Score.objects.filter(
            registration__enrolment__student__in=students_visible_to(self.request.user)
        ).select_related("component", "registration__subject", "registration__enrolment__student")

    @extend_schema(
        request=BulkScoreSerializer,
        responses={200: None, 422: None},
        description="Enter one component for a whole class in a single transaction. "
        "Rows carry the version the device last saw; a mismatch comes back as a "
        "per-row CONFLICT instead of overwriting an office correction. A null "
        "score removes the stored mark — the same version check applies.",
    )
    @action(detail=False, methods=["post"], permission_classes=[EnterScore])
    def bulk(self, request):
        serializer = BulkScoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        term, subject, component = data["term"], data["subject"], data["component"]
        class_arm = data.get("class_arm")

        staff = staff_for(request.user)
        unscoped = request.user.has_perm_code("assessment.view_all_scores")
        if not unscoped and not teaches(staff, subject=subject, class_arm=class_arm, term=term):
            return Response(
                error_payload("NOT_YOUR_SUBJECT", "You are not assigned to teach this subject."),
                status=403,
            )

        allowed = {str(pk) for pk in students_visible_to(request.user).values_list("pk", flat=True)}
        rows = [
            services.ScoreRow(
                student_id=str(row["student"]),
                score=row["score"],
                version=row.get("version"),
                recorded_at=row.get("recorded_at"),
            )
            for row in data["rows"]
        ]

        outcome = services.enter_scores(
            term=term,
            subject=subject,
            component=component,
            rows=rows,
            entered_by=staff,
            allowed_student_ids=allowed,
            # An exams officer may correct a published result; a teacher may not.
            override_window=request.user.has_perm_code("assessment.publish_results"),
        )
        return outcome.as_response()

    @extend_schema(
        responses={200: None},
        description="The entry screen for one subject and class: component columns "
        "plus one row per registered student with the marks already stored.",
    )
    @action(detail=False, methods=["get"], permission_classes=[EnterScore])
    def sheet(self, request):
        term = lookup_param(request, "term", Term, required=True)
        subject = lookup_param(request, "subject", Subject, required=True)
        class_arm = lookup_param(request, "class_arm", ClassArm)
        level = lookup_param(request, "level", ClassLevel)

        staff = staff_for(request.user)
        unscoped = request.user.has_perm_code("assessment.view_all_scores")
        if not unscoped and not teaches(
            staff, subject=subject, class_arm=class_arm, level=level, term=term
        ):
            return Response(
                error_payload("NOT_YOUR_SUBJECT", "You are not assigned to teach this subject."),
                status=403,
            )

        return Response(
            selectors.mark_sheet(
                term=term,
                subject=subject,
                class_arm=class_arm,
                level=level,
                students=students_visible_to(request.user),
            )
        )


class SubjectResultViewSet(TenantScopedViewSet, viewsets.ReadOnlyModelViewSet):
    """Computed per-subject results. Derived data, so read-only by design."""

    serializer_class = SubjectResultSerializer
    filterset_fields = ["registration__term", "registration__subject", "grade", "is_pass"]
    permission_classes = [IsAuthenticated]
    #: Scoping happens in `get_queryset`; this only tells the schema generator
    #: which model it is looking at.
    queryset = SubjectResult.objects.none()

    def get_queryset(self):
        if not selectors.may_read_results(self.request.user):
            return SubjectResult.objects.none()
        queryset = SubjectResult.objects.filter(
            registration__enrolment__student__in=students_visible_to(self.request.user)
        ).select_related("registration__subject", "registration__enrolment__student")
        if self.request.user.has_perm_code("assessment.view_all_scores"):
            return queryset
        # A student or guardian sees a subject result only once the term is out.
        return queryset.filter(registration__term__results_published_at__isnull=False)


class TermResultViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    """Term results, plus the actions that produce and publish them."""

    serializer_class = TermResultSerializer
    filterset_fields = ["term", "enrolment", "promotion_status"]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "post", "head", "options"]
    queryset = TermResult.objects.none()
    #: `report_card` returns a PDF, and content negotiation runs before
    #: dispatch: without the PDF renderer declared, a client asking for
    #: `Accept: application/pdf` is answered 406 and never reaches the action.
    renderer_classes = pdf.PDF_RENDERERS

    def get_queryset(self):
        return selectors.results_visible_to(self.request.user)

    def get_permissions(self):
        if self.action in {"partial_update"}:
            return [ViewAllScores()]
        return super().get_permissions()

    @extend_schema(request=ComputeTermSerializer, responses={200: None})
    @action(detail=False, methods=["post"], permission_classes=[ViewAllScores])
    def compute(self, request):
        """Rebuild every derived row for a term. Safe to run repeatedly."""
        serializer = ComputeTermSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.recompute_term(serializer.validated_data["term"]))

    @extend_schema(request=PublishTermSerializer, responses={200: None, 400: None})
    @action(detail=False, methods=["post"], permission_classes=[PublishResults])
    def publish(self, request):
        serializer = PublishTermSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        term = services.publish_results(
            serializer.validated_data["term"],
            actor=request.user,
            force=serializer.validated_data["force"],
        )
        return Response({"term": str(term.pk), "published_at": term.results_published_at})

    @extend_schema(request=ComputeTermSerializer, responses={200: None})
    @action(detail=False, methods=["post"], permission_classes=[PublishResults])
    def unpublish(self, request):
        serializer = ComputeTermSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        term = services.unpublish_results(serializer.validated_data["term"], actor=request.user)
        return Response({"term": str(term.pk), "published_at": None})

    @extend_schema(responses={200: None}, description="Report card as a PDF.")
    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated])
    def report_card(self, request, pk=None):
        term_result = self.get_object()
        context = selectors.report_card_context(term_result)
        context["branding"] = pdf.branding(getattr(request, "tenant", None))
        student = term_result.enrolment.student
        return pdf.pdf_response(
            "assessment/report_card.html",
            context,
            filename=f"report-{student.display_number}-{term_result.term.index}.pdf",
        )

    @extend_schema(request=PromotionOverrideSerializer, responses={200: TermResultSerializer})
    @action(detail=True, methods=["post"], permission_classes=[OverridePromotion])
    def override_promotion(self, request, pk=None):
        term_result = self.get_object()
        serializer = PromotionOverrideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.override_promotion(
            term_result=term_result,
            decision=serializer.validated_data["decision"],
            reason=serializer.validated_data["reason"],
            actor=request.user,
        )
        return Response(self.get_serializer(term_result).data)


class BroadsheetView(viewsets.ViewSet):
    """`/assessment/broadsheet/` — the whole cohort, as JSON or as a PDF."""

    permission_classes = [GenerateReport]
    renderer_classes = pdf.PDF_RENDERERS

    @extend_schema(responses={200: None})
    def list(self, request):
        term = lookup_param(request, "term", Term, required=True)
        class_arm = lookup_param(request, "class_arm", ClassArm)
        level = lookup_param(request, "level", ClassLevel)
        sheet = selectors.broadsheet(term=term, class_arm=class_arm, level=level)

        if request.query_params.get("format") == "pdf":
            return pdf.pdf_response(
                "assessment/broadsheet.html",
                {"sheet": sheet, "branding": pdf.branding(getattr(request, "tenant", None))},
                filename=f"broadsheet-{term.pk}.pdf",
            )
        return Response(sheet)


class TranscriptView(viewsets.ViewSet):
    """`/assessment/transcript/<student_id>/` — every term, oldest first."""

    permission_classes = [IsAuthenticated]
    renderer_classes = pdf.PDF_RENDERERS

    @extend_schema(
        parameters=[
            OpenApiParameter("id", OpenApiTypes.UUID, OpenApiParameter.PATH, description="Student")
        ],
        responses={200: None},
    )
    def retrieve(self, request, pk=None):
        student = get_object_or_404(Student.objects.alive(), pk=pk)
        if not can_view_student(request.user, student):
            return Response(error_payload("NOT_FOUND", "No such student."), status=404)

        transcript = selectors.transcript_context(student)
        if request.query_params.get("format") == "pdf":
            if not request.user.has_perm_code("assessment.generate_report"):
                return Response(
                    error_payload(
                        "FORBIDDEN", "Only the registrar's office may print a transcript."
                    ),
                    status=403,
                )
            tenant = getattr(request, "tenant", None)
            return pdf.pdf_response(
                "assessment/transcript.html",
                {
                    "transcript": transcript,
                    "branding": pdf.branding(tenant),
                    # Signed here rather than in the selector: only the printed
                    # sheet leaves the building and needs proving.
                    "verification": verification.stamp(
                        verification.TRANSCRIPT, student.pk, tenant=tenant
                    ),
                },
                filename=f"transcript-{student.display_number}.pdf",
            )

        transcript["student"] = {
            "id": str(student.pk),
            "full_name": student.full_name,
            "number": student.display_number,
        }
        return Response(transcript)


class PromotionView(viewsets.ViewSet):
    """Deciding and applying end-of-session promotions.

    Two steps on purpose: `decide` writes nothing anyone can see except the
    decision itself, so an exams officer can read the list, override the three
    rows they disagree with, and only then `apply`.
    """

    permission_classes = [OverridePromotion]
    serializer_class = PromotionRunSerializer

    @extend_schema(
        request=PromotionRunSerializer,
        responses={200: None},
        description="Dry run: who moves up, who repeats, who graduates.",
    )
    @action(detail=False, methods=["post"])
    def decide(self, request):
        serializer = PromotionRunSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            {"decisions": services.decide_promotions(session=serializer.validated_data["session"])}
        )

    @extend_schema(
        request=PromotionApplySerializer,
        responses={200: None},
        description="Apply the recorded decisions into next session's enrolments.",
    )
    @action(detail=False, methods=["post"])
    def apply(self, request):
        serializer = PromotionApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        counts = services.apply_promotions(
            session=serializer.validated_data["session"],
            next_session=serializer.validated_data["next_session"],
            actor=request.user,
        )
        return Response(counts)
