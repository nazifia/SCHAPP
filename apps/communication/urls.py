from rest_framework.routers import DefaultRouter

from .views import (
    AnnouncementViewSet,
    BulkSmsView,
    InboxViewSet,
    MessageLogViewSet,
    MessageTemplateViewSet,
)

app_name = "communication"

router = DefaultRouter()
router.register("announcements", AnnouncementViewSet, basename="announcement")
router.register("templates", MessageTemplateViewSet, basename="message-template")
router.register("inbox", InboxViewSet, basename="inbox")
router.register("messages", MessageLogViewSet, basename="message")
router.register("sms", BulkSmsView, basename="bulk-sms")

urlpatterns = router.urls
