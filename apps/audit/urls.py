from rest_framework.routers import DefaultRouter

from .views import AuditLogViewSet

app_name = "audit"

router = DefaultRouter()
router.register("entries", AuditLogViewSet, basename="audit-entry")

urlpatterns = router.urls
