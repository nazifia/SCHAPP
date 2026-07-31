"""Reading the audit trail.

`admin.view_audit` has been in the catalogue since Phase 2 with nothing
checking it: the trail was written diligently and readable only through the
Django admin, which is not where a principal is. Read-only by construction —
the model refuses to be updated or deleted at all, so there is nothing else
this could offer.
"""

from django_filters import rest_framework as filters
from rest_framework import viewsets

from apps.accounts.permissions import RequirePermission
from apps.api.mixins import TenantScopedViewSet

from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogFilter(filters.FilterSet):
    """The three questions actually asked of a trail: what happened to this
    record, what did this person do, and what happened that week."""

    since = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    until = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")
    action = filters.CharFilter(field_name="action", lookup_expr="startswith")

    class Meta:
        model = AuditLog
        fields = ["action", "actor", "object_type", "object_id", "succeeded", "since", "until"]


class AuditLogViewSet(TenantScopedViewSet, viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.all()
    serializer_class = AuditLogSerializer
    permission_classes = [RequirePermission("admin.view_audit")]
    filterset_class = AuditLogFilter
