"""Finance endpoints.

Three audiences: a bursar raising and reconciling bills, a parent paying one,
and a gateway posting a webhook at three in the morning. The first two are
ordinary authenticated viewsets; the third lives in `public_views` because a
payment processor holds no session and cannot send `X-Tenant-Slug`.
"""

from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django_filters import rest_framework as filters
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.academics.models import AcademicSession, Term
from apps.accounts.permissions import RequirePermission
from apps.api.deletion import ProtectDependentsMixin
from apps.api.exceptions import error_payload
from apps.api.mixins import TenantScopedViewSet, lookup_param
from apps.assessment import pdf
from apps.people.models import Student
from apps.people.selectors import can_view_student, staff_for

from . import selectors, services
from .gateways import configured_gateways
from .models import (
    FeeCategory,
    FeeItem,
    FeeStructure,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentStatus,
)
from .serializers import (
    AdjustInvoiceSerializer,
    CheckoutSerializer,
    FeeItemSerializer,
    FeeStructureSerializer,
    FinanceReportSerializer,
    GenerateInvoicesSerializer,
    InvoiceSerializer,
    PaymentSerializer,
    ReasonSerializer,
    RecordPaymentSerializer,
)

ViewInvoice = RequirePermission("finance.view_invoice")
ManageFees = RequirePermission("finance.manage_fees")
AdjustInvoice = RequirePermission("finance.adjust_invoice")
RecordPayment = RequirePermission("finance.record_payment")


class AuditedFeeEditMixin:
    """Every write to the price list leaves a row saying who moved the money.

    The edit itself is ordinary DRF; the interesting half is `fee_changed`,
    which also carries a new price into the drafts raised from it.
    """

    def perform_create(self, serializer):
        super().perform_create(serializer)
        services.fee_changed(serializer.instance, actor=self.request.user, request=self.request)

    def perform_update(self, serializer):
        # Read the old values off the instance *before* the save overwrites them.
        before = {
            field: str(getattr(serializer.instance, field, ""))
            for field in serializer.validated_data
        }
        super().perform_update(serializer)
        services.fee_changed(
            serializer.instance, before=before, actor=self.request.user, request=self.request
        )


class FeeStructureViewSet(
    AuditedFeeEditMixin, ProtectDependentsMixin, TenantScopedViewSet, viewsets.ModelViewSet
):
    """The price list. Everyone signed in may read it; the bursar sets it.

    Deleting one takes its `FeeItem` rows with it, and the invoices raised
    from it keep their snapshotted lines but lose the link back
    (`Invoice.structure` is SET_NULL). `is_active` is how a price list is
    retired without unpicking last term's billing.
    """

    queryset = FeeStructure.objects.prefetch_related("items").select_related(
        "session", "term", "level", "programme"
    )
    serializer_class = FeeStructureSerializer
    filterset_fields = ["session", "term", "level", "programme", "is_active"]

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [ManageFees()]


class FeeItemViewSet(AuditedFeeEditMixin, TenantScopedViewSet, viewsets.ModelViewSet):
    queryset = FeeItem.objects.select_related("structure")
    serializer_class = FeeItemSerializer
    filterset_fields = ["structure", "category", "is_optional"]

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [ManageFees()]

    @extend_schema(
        responses={200: None},
        description="What a fee line may be charged for — `FeeCategory`, in its "
        "order — so a client's picker offers exactly what the server accepts.",
    )
    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def categories(self, request):
        return Response(
            [{"value": value, "label": label} for value, label in FeeCategory.choices]
        )


class InvoiceFilter(filters.FilterSet):
    """`?owing=true` — the bursary's working list.

    A counter takes money against a bill that is still open *and* still has a
    balance, which is two ISSUED/PART_PAID statuses plus an arithmetic
    condition, and no combination of exact `status=` values expresses it. A
    DRAFT is deliberately excluded: it is invisible to the payer, so nobody is
    standing there with it in their hand.
    """

    owing = filters.BooleanFilter(method="filter_owing")

    class Meta:
        model = Invoice
        fields = ["student", "session", "term", "status", "structure", "owing"]

    def filter_owing(self, queryset, name, value):
        if value is None:
            return queryset
        open_and_unpaid = Q(
            status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PART_PAID],
            total__gt=F("amount_paid"),
        )
        return queryset.filter(open_and_unpaid if value else ~open_and_unpaid)


class InvoiceViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    """Bills. Read is scoped to the bursar's office or the payer."""

    serializer_class = InvoiceSerializer
    filterset_class = InvoiceFilter
    #: What a bursar types with a parent standing at the counter: the number on
    #: the printed bill, or the child's name, or the admission number off the
    #: ID card. `SearchFilter` is in DEFAULT_FILTER_BACKENDS, so this is read.
    search_fields = [
        "number",
        "student__first_name",
        "student__last_name",
        "student__admission_number",
        "student__matriculation_number",
    ]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "head", "options"]
    queryset = Invoice.objects.none()

    def get_queryset(self):
        return selectors.invoices_visible_to(self.request.user).prefetch_related("lines")

    def get_permissions(self):
        if self.action == "create":
            return [ManageFees()]
        # Raising a bill is the bursar's; changing one a family is already
        # holding — its discount, its note, its due date — is an adjustment.
        if self.action == "partial_update":
            return [AdjustInvoice()]
        return super().get_permissions()

    @extend_schema(
        request=AdjustInvoiceSerializer,
        responses={200: InvoiceSerializer, 400: None},
        description="Rewrite this bill's lines, and optionally its discount, with a "
        "reason for the trail. `lines` replaces the whole bill. Refused on a "
        "cancelled or waived invoice, or where the new total is below what has "
        "already been paid.",
    )
    @action(detail=True, methods=["post"], permission_classes=[AdjustInvoice])
    def adjust(self, request, pk=None):
        serializer = AdjustInvoiceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        invoice = services.adjust_invoice(
            self.get_object(),
            lines=data["lines"],
            discount=data.get("discount"),
            reason=data["reason"],
            actor=request.user,
            request=request,
        )
        return Response(self.get_serializer(invoice).data)

    @extend_schema(
        request=GenerateInvoicesSerializer,
        responses={200: None, 422: None},
        description="Raise one invoice per student in a fee structure's cohort. "
        "Running it again bills only the students who joined since.",
    )
    @action(detail=False, methods=["post"], permission_classes=[ManageFees])
    def generate(self, request):
        serializer = GenerateInvoicesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        outcome = services.generate_invoices(
            structure=serializer.validated_data["structure"],
            student_ids=serializer.validated_data.get("students"),
            actor=request.user,
        )
        return outcome.as_response()

    @extend_schema(responses={200: InvoiceSerializer})
    @action(detail=True, methods=["post"], permission_classes=[ManageFees])
    def issue(self, request, pk=None):
        invoice = services.issue_invoice(self.get_object(), actor=request.user, request=request)
        return Response(self.get_serializer(invoice).data)

    @extend_schema(request=ReasonSerializer, responses={200: InvoiceSerializer})
    @action(detail=True, methods=["post"], permission_classes=[ManageFees])
    def waive(self, request, pk=None):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = services.waive_invoice(
            self.get_object(),
            reason=serializer.validated_data["reason"],
            actor=request.user,
            request=request,
        )
        return Response(self.get_serializer(invoice).data)

    @extend_schema(request=ReasonSerializer, responses={200: InvoiceSerializer, 400: None})
    @action(detail=True, methods=["post"], permission_classes=[ManageFees])
    def cancel(self, request, pk=None):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = services.cancel_invoice(
            self.get_object(),
            reason=serializer.validated_data["reason"],
            actor=request.user,
            request=request,
        )
        return Response(self.get_serializer(invoice).data)

    @extend_schema(
        request=RecordPaymentSerializer,
        responses={201: PaymentSerializer, 400: None},
        description="Cash, transfer or POS taken at the counter.",
    )
    @action(detail=True, methods=["post"], permission_classes=[RecordPayment])
    def pay(self, request, pk=None):
        serializer = RecordPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = services.record_payment(
            invoice=self.get_object(),
            recorded_by=staff_for(request.user),
            actor=request.user,
            request=request,
            **serializer.validated_data,
        )
        return Response(PaymentSerializer(payment).data, status=201)

    @extend_schema(
        request=CheckoutSerializer,
        responses={200: None, 400: None},
        description="Open a gateway checkout for this invoice and return the URL "
        "to send the payer to. The payment is created PENDING and settled by the "
        "webhook or by `payments/{id}/verify/`.",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def checkout(self, request, pk=None):
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        payment, charge = services.start_online_payment(
            invoice=self.get_object(),
            gateway_name=data["gateway"],
            tenant=getattr(request, "tenant", None),
            amount=data.get("amount"),
            email=data.get("email", "") or getattr(request.user, "email", ""),
            callback_url=data.get("callback_url", ""),
            payer_phone=getattr(request.user, "phone", "") or "",
        )
        return Response(
            {
                "payment": str(payment.pk),
                "reference": payment.reference,
                "amount": payment.amount,
                "checkout_url": charge.checkout_url,
                "gateway": payment.gateway,
            }
        )

    @extend_schema(responses={200: None}, description="The invoice as a PDF.")
    @action(
        detail=True,
        methods=["get"],
        permission_classes=[IsAuthenticated],
        # Content negotiation runs before dispatch: without the PDF renderer
        # declared, a client sending `Accept: application/pdf` is answered 406
        # and never reaches this action.
        renderer_classes=pdf.PDF_RENDERERS,
    )
    def document(self, request, pk=None):
        invoice = self.get_object()
        return pdf.pdf_response(
            "finance/invoice.html",
            {
                "invoice": invoice,
                "lines": invoice.lines.all(),
                "payments": invoice.payments.exclude(status=PaymentStatus.PENDING),
                "branding": pdf.branding(getattr(request, "tenant", None)),
            },
            filename=f"invoice-{invoice.number.replace('/', '-')}.pdf",
        )

    @extend_schema(
        responses={200: None},
        description="Gateways this institution can actually charge through.",
    )
    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def gateways(self, request):
        return Response({"gateways": configured_gateways(getattr(request, "tenant", None))})


class PaymentViewSet(TenantScopedViewSet, viewsets.ReadOnlyModelViewSet):
    """Receipts. Written through the invoice actions, never posted directly."""

    serializer_class = PaymentSerializer
    filterset_fields = ["invoice", "status", "method", "gateway"]
    permission_classes = [IsAuthenticated]
    queryset = Payment.objects.none()

    def get_queryset(self):
        return selectors.payments_visible_to(self.request.user)

    @extend_schema(
        responses={200: PaymentSerializer},
        description="Ask the gateway what happened to this reference. The "
        "fallback when a webhook never arrived; safe to call repeatedly.",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def verify(self, request, pk=None):
        payment = services.verify_payment(
            self.get_object(), tenant=getattr(request, "tenant", None), request=request
        )
        return Response(self.get_serializer(payment).data)

    @extend_schema(request=ReasonSerializer, responses={200: PaymentSerializer, 400: None})
    @action(detail=True, methods=["post"], permission_classes=[RecordPayment])
    def reverse(self, request, pk=None):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = services.reverse_payment(
            self.get_object(),
            reason=serializer.validated_data["reason"],
            actor=request.user,
            request=request,
        )
        return Response(self.get_serializer(payment).data)

    @extend_schema(responses={200: None}, description="Receipt as a PDF.")
    @action(
        detail=True,
        methods=["get"],
        permission_classes=[IsAuthenticated],
        renderer_classes=pdf.PDF_RENDERERS,
    )
    def receipt(self, request, pk=None):
        payment = self.get_object()
        if payment.status != PaymentStatus.SUCCESS:
            return Response(
                error_payload("NO_RECEIPT", "A receipt exists only for a successful payment."),
                status=400,
            )
        return pdf.pdf_response(
            "finance/receipt.html",
            {
                "payment": payment,
                "invoice": payment.invoice,
                "branding": pdf.branding(getattr(request, "tenant", None)),
            },
            filename=f"receipt-{payment.reference.replace('/', '-')}.pdf",
        )


class StatementView(viewsets.ViewSet):
    """`/finance/statement/<student_id>/` — every bill and receipt, oldest first."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter("id", OpenApiTypes.UUID, OpenApiParameter.PATH, description="Student")
        ],
        responses={200: None},
    )
    def retrieve(self, request, pk=None):
        student = get_object_or_404(Student.objects.alive(), pk=pk)
        allowed = request.user.has_perm_code("finance.view_invoice") or (
            request.user.has_perm_code("self.view_own_invoice")
            and can_view_student(request.user, student)
        )
        if not allowed:
            return Response(error_payload("NOT_FOUND", "No such student."), status=404)
        return Response(
            {
                "student": {
                    "id": str(student.pk),
                    "full_name": student.full_name,
                    "number": student.display_number,
                },
                **selectors.statement(student),
            }
        )


class FinanceReportView(viewsets.ViewSet):
    """`/finance/reports/` — what the term billed and what came in."""

    permission_classes = [ViewInvoice]
    serializer_class = FinanceReportSerializer

    def _scope(self, request):
        session = lookup_param(request, "session", AcademicSession)
        term = lookup_param(request, "term", Term)
        return session, term

    @extend_schema(responses={200: None})
    def list(self, request):
        session, term = self._scope(request)
        return Response(selectors.collection_summary(session=session, term=term))

    @extend_schema(responses={200: None}, description="Overdue bills, biggest balance first.")
    @action(detail=False, methods=["get"])
    def defaulters(self, request):
        session, term = self._scope(request)
        return Response({"defaulters": selectors.defaulters(session=session, term=term)})

    @extend_schema(
        responses={200: None},
        description="Re-verify online payments whose webhook never arrived.",
    )
    @action(detail=False, methods=["post"], permission_classes=[RecordPayment])
    def sweep(self, request):
        return Response(services.sweep_pending(tenant=getattr(request, "tenant", None)))
