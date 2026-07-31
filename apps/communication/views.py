"""Announcements, the inbox, and the bursar's bulk-SMS button."""

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import RequirePermission
from apps.api.mixins import TenantScopedViewSet
from apps.numbering.msisdn import normalize

from . import selectors, services
from .models import Announcement, Message, MessageTemplate
from .serializers import (
    AnnouncementSerializer,
    BulkSmsSerializer,
    MessageSerializer,
    MessageTemplateSerializer,
)

ManageAnnouncement = RequirePermission("communication.manage_announcement")
SendSms = RequirePermission("communication.send_sms")


class AnnouncementViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    """Notices. A draft is invisible until `publish` fans it out."""

    serializer_class = AnnouncementSerializer
    filterset_fields = ["level", "class_arm", "is_pinned"]
    permission_classes = [IsAuthenticated]
    queryset = Announcement.objects.none()

    def get_queryset(self):
        return selectors.announcements_visible_to(self.request.user).select_related("author")

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [ManageAnnouncement()]

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)

    @extend_schema(
        responses={200: None},
        description="Resolve the audience, write everyone an in-app copy and "
        "broadcast on the chosen channels. Publishing twice is a no-op.",
    )
    @action(detail=True, methods=["post"], permission_classes=[ManageAnnouncement])
    def publish(self, request, pk=None):
        return Response(
            services.publish_announcement(
                self.get_object(),
                actor=request.user,
                request=request,
                tenant=getattr(request, "tenant", None),
            )
        )


class MessageTemplateViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    queryset = MessageTemplate.objects.all()
    serializer_class = MessageTemplateSerializer
    filterset_fields = ["channel", "is_active"]
    permission_classes = [ManageAnnouncement]


class InboxViewSet(TenantScopedViewSet, viewsets.ReadOnlyModelViewSet):
    """`/communication/inbox/` — this user's in-app messages."""

    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]
    queryset = Message.objects.none()

    def get_queryset(self):
        return selectors.inbox(self.request.user)

    @extend_schema(request=None, responses={200: None})
    @action(detail=False, methods=["post"])
    def read(self, request):
        """Mark everything read. One call, because that is what the UI does."""
        updated = self.get_queryset().filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response({"read": updated})

    @extend_schema(responses={200: None})
    @action(detail=False, methods=["get"])
    def unread_count(self, request):
        return Response({"unread": self.get_queryset().filter(read_at__isnull=True).count()})


class MessageLogViewSet(TenantScopedViewSet, viewsets.ReadOnlyModelViewSet):
    """Everything the school sent. The answer to "was it delivered?"."""

    queryset = Message.objects.select_related("announcement")
    serializer_class = MessageSerializer
    filterset_fields = ["channel", "status", "announcement", "user"]
    permission_classes = [SendSms]


class BulkSmsView(viewsets.ViewSet):
    """`/communication/sms/` — free-text SMS to a list of numbers."""

    permission_classes = [SendSms]
    serializer_class = BulkSmsSerializer

    @extend_schema(request=BulkSmsSerializer, responses={200: None})
    def create(self, request):
        serializer = BulkSmsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phones = [normalize(phone) for phone in serializer.validated_data["phones"]]
        return Response(
            services.send_bulk_sms(
                phones=phones,
                body=serializer.validated_data["body"],
                tenant=getattr(request, "tenant", None),
                actor=request.user,
                request=request,
            )
        )
