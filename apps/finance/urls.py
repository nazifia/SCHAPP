from rest_framework.routers import DefaultRouter

from .views import (
    FeeItemViewSet,
    FeeStructureViewSet,
    FinanceReportView,
    InvoiceViewSet,
    PaymentViewSet,
    StatementView,
)

app_name = "finance"

router = DefaultRouter()
router.register("fee-structures", FeeStructureViewSet, basename="fee-structure")
router.register("fee-items", FeeItemViewSet, basename="fee-item")
router.register("invoices", InvoiceViewSet, basename="invoice")
router.register("payments", PaymentViewSet, basename="payment")
router.register("statement", StatementView, basename="statement")
router.register("reports", FinanceReportView, basename="finance-report")

urlpatterns = router.urls
