from rest_framework.routers import DefaultRouter

from .views import (
    ApplicationViewSet,
    GuardianViewSet,
    StaffViewSet,
    StudentDocumentViewSet,
    StudentViewSet,
)

app_name = "people"

router = DefaultRouter()
router.register("students", StudentViewSet, basename="student")
router.register("guardians", GuardianViewSet, basename="guardian")
router.register("staff", StaffViewSet, basename="staff")
router.register("applications", ApplicationViewSet, basename="application")
router.register("documents", StudentDocumentViewSet, basename="document")

urlpatterns = router.urls
