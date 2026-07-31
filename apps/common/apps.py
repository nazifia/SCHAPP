from django.apps import AppConfig


class CommonConfig(AppConfig):
    name = "apps.common"
    label = "common"

    def ready(self):
        # Listed after `django.contrib.admin` in SHARED_APPS, so autodiscovery
        # has already imported every admin.py by the time this runs.
        from apps.tenants.admin import gate_tenant_only_admins, pin_admin_content_types

        gate_tenant_only_admins()
        pin_admin_content_types()
