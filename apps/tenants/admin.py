from django.contrib import admin, messages
from django.db import DEFAULT_DB_ALIAS
from django.shortcuts import redirect

from . import services
from .admin_switch import enter_tenant, is_platform_superuser
from .db import current_alias, database_exists, shared_labels, tenant_labels
from .models import Domain, Plan, Tenant, TenantConfiguration
from .tasks import provision_tenant


class DomainInline(admin.TabularInline):
    model = Domain
    extra = 0


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "institution_type", "status", "plan", "provisioned_at")
    list_filter = ("status", "institution_type", "plan")
    search_fields = ("name", "slug", "contact_email")
    readonly_fields = ("schema_name", "provisioned_at", "provisioning_error")
    inlines = [DomainInline]
    actions = ["work_inside", "reprovision", "suspend_tenants", "reactivate_tenants"]

    @admin.action(description="Work inside this institution")
    def work_inside(self, request, queryset):
        """Select one school and reload the admin against its database.

        The same switch the header selector performs, offered from the list
        where a support request usually starts. One at a time by construction:
        there is one active tenant, not a set of them.
        """
        if not is_platform_superuser(request.user):
            self.message_user(
                request, "Only a platform superuser can do this.", level=messages.ERROR
            )
            return None
        if queryset.count() != 1:
            self.message_user(request, "Pick exactly one institution.", level=messages.ERROR)
            return None

        tenant = queryset.get()
        if not database_exists(tenant.schema_name):
            self.message_user(
                request,
                f"{tenant.name} has no database yet. Re-run provisioning first.",
                level=messages.ERROR,
            )
            return None

        enter_tenant(request, tenant)
        return redirect("admin:index")

    @admin.action(description="Re-run provisioning")
    def reprovision(self, request, queryset):
        for tenant in queryset:
            provision_tenant.delay(str(tenant.pk), force=True)
        self.message_user(request, f"Queued {queryset.count()} tenant(s) for provisioning.")

    # Editing `status` in the form sets the status and nothing else, leaving
    # `suspended_at` and `suspension_reason` describing a suspension that is
    # over — or none at all. These go through the services, which move the
    # three fields together and log it.
    @admin.action(description="Suspend (non-payment or abuse)")
    def suspend_tenants(self, request, queryset):
        for tenant in queryset:
            services.suspend(
                tenant,
                reason=f"Suspended by {request.user} in the platform admin.",
                actor=request.user,
            )
        self.message_user(request, f"Suspended {queryset.count()} tenant(s).")

    @admin.action(description="Reactivate")
    def reactivate_tenants(self, request, queryset):
        for tenant in queryset:
            services.reactivate(tenant, actor=request.user)
        self.message_user(request, f"Reactivated {queryset.count()} tenant(s).")


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "price_ngn", "trial_days", "is_public")


@admin.register(TenantConfiguration)
class TenantConfigurationAdmin(admin.ModelAdmin):
    list_display = ("tenant", "sms_sender_id", "require_nin")
    # Secrets are write-only in the UI; they are never listed.
    exclude = ("sms_credentials", "paystack_secret_key", "flutterwave_secret_key")


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "tenant", "is_primary", "is_custom", "verified_at")
    search_fields = ("domain",)


# ---------------------------------------------------------------------------
# Tenant-only models in the admin
#
# `/admin/` is a public path, so it is reachable on the platform host with no
# tenant selected — and there a tenant-only model has no database to read: the
# router raises TenantContextRequired rather than quietly using `default`. Those
# pages are hidden instead of crashing. They come back the moment a tenant *is*
# selected, by any of the three routes there are: a school's own host
# (`<slug>.<BASE_DOMAIN>/admin/`), the header selector, or the changelist
# action above — all of which end in `db.set_tenant`, which is what this reads.
# ---------------------------------------------------------------------------
def _tenant_selected() -> bool:
    return current_alias() != DEFAULT_DB_ALIAS


class TenantOnlyAdminMixin:
    """A ModelAdmin that does not exist outside a tenant."""

    def has_module_permission(self, request):
        return _tenant_selected() and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return _tenant_selected() and super().has_view_permission(request, obj)

    def has_add_permission(self, request, *args):
        # Inline admins pass the parent object; plain ones pass nothing.
        return _tenant_selected() and super().has_add_permission(request, *args)

    def has_change_permission(self, request, obj=None):
        return _tenant_selected() and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return _tenant_selected() and super().has_delete_permission(request, obj)


def gate_tenant_only_admins(site: admin.AdminSite | None = None) -> None:
    """Re-register every tenant-only ModelAdmin behind :class:`TenantOnlyAdminMixin`.

    Called once from ``apps.common``'s ``AppConfig.ready``, which runs after
    ``django.contrib.admin`` has autodiscovered every ``admin.py``.
    """
    site = site or admin.site
    tenant_only = tenant_labels() - shared_labels()

    for model, model_admin in list(site._registry.items()):
        if model._meta.app_label not in tenant_only:
            continue
        cls = type(model_admin)
        if issubclass(cls, TenantOnlyAdminMixin):
            continue
        site.unregister(model)
        site.register(model, type(cls.__name__, (TenantOnlyAdminMixin, cls), {}))


def pin_admin_content_types() -> None:
    """Make admin history resolve its content types on the platform connection.

    ``LogEntry`` is in ``django.contrib.admin``, which is a shared app, so the
    router always writes it to ``default``. Its ``content_type`` is *not*
    routed with it: ``ContentType.objects.get_for_model`` obeys the router like
    anything else, so inside a tenant it answers with an id from that school's
    ``django_content_type`` table. The two tables list different apps — the
    platform's half of the split and the school's — so their ids do not line
    up. An unpinned entry would either name the wrong model on the history page
    or, more often, point at an id ``default`` does not have at all and fail the
    foreign key: saving *any* object from inside a tenant would raise.

    Pinning is unconditional rather than tenant-aware, because it is not a
    workaround for the switcher: the row lives in the platform database, so its
    content type belongs to the platform database, always. Django reaches for
    the model through exactly two names, and both are replaced here.

    ponytail: ``admin:view_on_site`` still resolves its id through the router,
    so a model with ``get_absolute_url`` would follow a platform id into a
    tenant table. Nothing in this project defines one; add the same pin to
    ``django.contrib.contenttypes.views`` when something does.
    """
    from django.contrib.admin import models as admin_models
    from django.contrib.admin import options as admin_options
    from django.contrib.contenttypes.models import ContentType

    def platform_manager():
        return ContentType.objects.db_manager(DEFAULT_DB_ALIAS)

    class _PlatformContentType:
        """Stands in for the ``ContentType`` name inside ``admin.models``.

        Only ``.objects`` is ever touched there; the ``content_type`` foreign
        key resolved the real class at import time and is unaffected.
        """

        @property
        def objects(self):
            return platform_manager()

    admin_options.get_content_type_for_model = lambda obj: platform_manager().get_for_model(
        obj, for_concrete_model=False
    )
    admin_models.ContentType = _PlatformContentType()
