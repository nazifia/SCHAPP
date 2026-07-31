from django.core.management.base import BaseCommand, CommandError

from apps.tenants.models import InstitutionType, Plan
from apps.tenants.services import create_tenant
from apps.tenants.tasks import provision_tenant


class Command(BaseCommand):
    help = "Register a school and provision its database."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("name")
        parser.add_argument(
            "--type", choices=[c for c, _ in InstitutionType.choices], default="SECONDARY"
        )
        parser.add_argument("--plan", default=None)
        parser.add_argument("--email", default="")
        parser.add_argument("--phone", default="")
        parser.add_argument(
            "--sync", action="store_true", help="Provision inline instead of via Celery"
        )

    def handle(self, *args, **o):
        plan = Plan.objects.filter(code=o["plan"]).first() if o["plan"] else None
        if o["plan"] and plan is None:
            raise CommandError(f"No plan with code {o['plan']!r}")

        tenant = create_tenant(
            name=o["name"],
            slug=o["slug"],
            institution_type=o["type"],
            contact_email=o["email"],
            contact_phone=o["phone"],
            plan=plan,
            consented=True,
        )
        if o["sync"]:
            result = provision_tenant(str(tenant.pk))
            self.stdout.write(self.style.SUCCESS(str(result)))
        else:
            self.stdout.write(self.style.SUCCESS(f"{tenant.slug} queued for provisioning"))
