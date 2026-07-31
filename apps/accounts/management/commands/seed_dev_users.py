"""One test user per role, per tenant, with predictable phone numbers.

Dev fixture only: real accounts arrive through onboarding. Idempotent, so
re-running after a `migrate_tenants` costs nothing.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role, User
from apps.tenants.db import schema_context
from apps.tenants.models import Tenant

#: Phones are `0803<tenant><role:06d>` — 11 digits, MTN prefix, no chance of
#: colliding with a number a person actually owns.
PHONE_PREFIX = "0803"


class Command(BaseCommand):
    help = "Create one test user per role in every provisioned tenant."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            action="append",
            dest="tenants",
            help="Slug to seed; repeatable. Default: every provisioned tenant.",
        )
        parser.add_argument(
            "--password",
            default="",
            help="Shared password. Omitted means OTP-only accounts, which is the norm.",
        )

    def handle(self, *args, **o):
        # The index is fixed by slug order across *all* tenants, never by the
        # subset being seeded: `--tenant x` has to keep handing x the same
        # numbers it got on a full run.
        every = list(
            Tenant.objects.exclude(slug="public")
            .filter(provisioned_at__isnull=False)
            .order_by("slug")
        )
        numbered = list(enumerate(every, start=1))
        if o["tenants"]:
            wanted = set(o["tenants"])
            missing = wanted - {t.slug for t in every}
            if missing:
                raise CommandError(f"Not provisioned: {', '.join(sorted(missing))}")
            numbered = [(i, t) for i, t in numbered if t.slug in wanted]
        if not numbered:
            raise CommandError("No provisioned tenants to seed.")

        for index, tenant in numbered:
            self.stdout.write(self.style.MIGRATE_HEADING(f"{tenant.slug}"))
            with schema_context(tenant.schema_name):
                self._seed(tenant, index, o["password"])

    def _seed(self, tenant, index: int, password: str) -> None:
        for serial, role in enumerate(Role.objects.order_by("code"), start=1):
            phone = f"{PHONE_PREFIX}{index}{serial:06d}"
            # Skip the manager so a second run does not reset a password or a
            # PIN that was set by hand while testing.
            user = User.objects.filter(phone=f"+234{phone[1:]}").first()
            created = user is None
            if created:
                user = User.objects.create_user(
                    phone,
                    password or None,
                    first_name=role.name.split("/")[0].strip(),
                    last_name=tenant.name.split()[0],
                )
            user.roles.add(role)
            flag = "+" if created else "="
            self.stdout.write(f"  {flag} {user.phone}  {role.code}")
