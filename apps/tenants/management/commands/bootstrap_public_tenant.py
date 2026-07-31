"""Create the `public` tenant row the platform database needs before anything works.

Run once per environment, right after the first `manage.py migrate`.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.tenants.models import Domain, Tenant, TenantStatus


class Command(BaseCommand):
    help = "Create the public tenant and its domains (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--domain", default=None, help="Defaults to settings.BASE_DOMAIN")

    def handle(self, *args, **options):
        tenant, created = Tenant.objects.get_or_create(
            schema_name=settings.PUBLIC_SCHEMA_NAME,
            defaults={
                "name": "Platform",
                "slug": "public",
                "status": TenantStatus.ACTIVE,
            },
        )
        hostname = options["domain"] or settings.BASE_DOMAIN
        for host in {hostname, "localhost", "127.0.0.1"}:
            Domain.objects.get_or_create(
                domain=host, tenant=tenant, defaults={"is_primary": host == hostname}
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"public tenant {'created' if created else 'already present'} ({hostname})"
            )
        )
