"""Run migrations against every tenant database.

`manage.py migrate` only touches the platform database. A deployment that adds
a column to a tenant app is not finished until this has run.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.tenants import db
from apps.tenants.models import Tenant


class Command(BaseCommand):
    help = "Migrate every provisioned tenant database (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--slug", default=None, help="Migrate one tenant instead of all")
        parser.add_argument(
            "--keep-going",
            action="store_true",
            help="Report failures and continue instead of stopping at the first one.",
        )

    def handle(self, *args, **options):
        tenants = Tenant.objects.exclude(schema_name="public").order_by("slug")
        if options["slug"]:
            tenants = tenants.filter(slug=options["slug"])
            if not tenants.exists():
                raise CommandError(f"No tenant with slug {options['slug']!r}")
        tenants = tenants.filter(provisioned_at__isnull=False)

        failures = []
        for tenant in tenants:
            self.stdout.write(f"migrating {tenant.slug} ({tenant.schema_name})")
            try:
                db.create_database(tenant.schema_name, verbosity=options["verbosity"])
            except Exception as exc:
                if not options["keep_going"]:
                    raise CommandError(f"{tenant.slug}: {exc}") from exc
                failures.append((tenant.slug, exc))
                self.stderr.write(self.style.ERROR(f"{tenant.slug}: {exc}"))

        done = tenants.count() - len(failures)
        self.stdout.write(self.style.SUCCESS(f"{done} tenant database(s) migrated"))
        if failures:
            raise CommandError(f"{len(failures)} tenant(s) failed: {[s for s, _ in failures]}")
