"""List-endpoint machinery shared by every module.

Built for the network these apps actually run on: a teacher on 3G in a
staffroom. Three things follow from that —

* `updated_since` so a re-sync transfers deltas, not the whole class list;
* ETag / `If-None-Match` so an unchanged list costs a 304, not a payload;
* `?expand=` so one request returns the related rows instead of the client
  making forty follow-up calls.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from django.utils.http import quote_etag
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response


def lookup_param(request, name: str, model, *, required: bool = False):
    """Resolve `?<name>=<id>` to a record, or fail with the right status.

    The reports take their scope from the query string rather than the path
    and hand-rolled every lookup, which gave three different mistakes one
    answer each, all wrong. A missing parameter came back 404 — "no such
    term" — and sent the caller hunting a record they never asked for. A
    malformed id went to the ORM as written and escaped as an unhandled
    `django.core.exceptions.ValidationError`, so a typo in a URL was a 500.
    An id that matched nothing silently dropped the filter, so scoping a
    report to a deleted term returned *every* term instead of none.

    So: absent is 400 when the endpoint needs it and `None` when it does not,
    malformed is always 400 — both are the caller's mistake, and 400 is
    already what DRF's filter backend answers on the list endpoints next
    door — and 404 keeps its one honest meaning, a well-formed id matching
    nothing.
    """
    value = request.query_params.get(name)
    if not value:
        if required:
            raise ValidationError({name: ["This query parameter is required."]})
        return None
    try:
        record = model._default_manager.filter(pk=value).first()
    except (DjangoValidationError, ValueError) as exc:
        raise ValidationError({name: ["Not a valid id."]}) from exc
    if record is None:
        raise Http404(f"No {model._meta.verbose_name} matches {name}={value}.")
    return record


class UpdatedSinceMixin:
    """`?updated_since=<iso8601>` — the delta half of offline sync.

    Rows deleted server-side are invisible to this filter; clients reconcile
    deletions with a periodic full pull. Soft-deleted rows still come back
    with `deleted_at` set so the client can drop them locally.
    """

    updated_since_field = "updated_at"

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        raw = self.request.query_params.get("updated_since")
        if not raw:
            return queryset
        from django.utils.dateparse import parse_datetime

        since = parse_datetime(raw)
        if since is None:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"updated_since": "Use an ISO-8601 timestamp."})
        return queryset.filter(**{f"{self.updated_since_field}__gt": since})


class ETagMixin:
    """Weak ETag from (row count, newest updated_at).

    Cheap: one aggregate query instead of serialising the page. It cannot
    detect a change that leaves both the count and the maximum timestamp
    untouched, which our `auto_now` timestamps make impossible in practice.
    """

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        etag = self._compute_etag(queryset)
        if etag and request.headers.get("If-None-Match") == etag:
            return Response(status=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

        response = super().list(request, *args, **kwargs)
        if etag:
            response["ETag"] = etag
        return response

    def _compute_etag(self, queryset) -> str | None:
        import hashlib

        from django.db.models import Count, Max

        try:
            stats = queryset.aggregate(n=Count("pk"), latest=Max("updated_at"))
        except Exception:
            return None
        latest = stats["latest"].timestamp() if stats["latest"] else 0
        # The query string is part of the representation, and the aggregate
        # above cannot see it: `?cursor=`, `?expand=` and `?page_size=` all
        # change the body while leaving the filtered queryset identical. Every
        # page of a list therefore carried one ETag, so a client keying its
        # cache on the path — which is what the offline sync does — asks for
        # page two with page one's tag and is told 304. A differing query
        # string that would in fact render the same body only costs a missed
        # cache hit, which is the safe direction to be wrong in.
        variant = hashlib.sha1(
            self.request.META.get("QUERY_STRING", "").encode(), usedforsecurity=False
        ).hexdigest()[:8]
        return quote_etag(f"{stats['n']}-{latest}-{variant}")


class ExpandableSerializerMixin:
    """`?expand=student,subject` swaps ids for nested objects.

    Declare `expandable_fields = {"student": (StudentSerializer, {"read_only": True})}`.
    Without it a mobile client building a mark sheet makes one request per
    row; with it, one request.
    """

    expandable_fields: dict = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self._requested_expansions():
            spec = self.expandable_fields.get(name)
            if spec is None:
                continue
            serializer_class, options = spec
            self.fields[name] = serializer_class(**{"read_only": True, **options})

    def _requested_expansions(self) -> set[str]:
        request = self.context.get("request")
        if request is None:
            return set()
        raw = request.query_params.get("expand", "")
        return {part.strip() for part in raw.split(",") if part.strip()}


class TenantScopedViewSet(ETagMixin, UpdatedSinceMixin):
    """Marker base for tenant-schema viewsets.

    Isolation itself comes from the schema, not from a filter — this only
    bundles the sync behaviour so no module forgets it.
    """

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant"] = getattr(self.request, "tenant", None)
        return context
