from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import BiometricWebhookView, StaffAttendanceViewSet, StudentAttendanceViewSet

app_name = "attendance"

router = DefaultRouter()
router.register("students", StudentAttendanceViewSet, basename="student-attendance")
router.register("staff", StaffAttendanceViewSet, basename="staff-attendance")

urlpatterns = [
    *router.urls,
    path(
        "biometric/<str:device_code>/",
        BiometricWebhookView.as_view(),
        name="biometric-webhook",
    ),
]
