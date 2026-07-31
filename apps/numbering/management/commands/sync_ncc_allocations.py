"""Load the NCC Mobile Number Allocation Table from a versioned fixture.

Re-runnable. This is the whole point of the design: when the NCC assigns a new
block, you edit the JSON and run this. No Python change, no deploy.
"""

import json
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.numbering.models import MobileNumberAllocation
from apps.numbering.selectors import invalidate_allocation_cache

DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "ncc_mobile_allocations.json"


class Command(BaseCommand):
    help = "Sync MobileNumberAllocation rows from an NCC fixture (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(DEFAULT_FIXTURE))
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Mark NDCs absent from the fixture as WITHDRAWN instead of leaving them.",
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"Fixture not found: {path}")

        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload.get("entries") or []
        if not entries:
            raise CommandError("Fixture contains no entries.")

        source_url = payload.get("source_url", "")
        verified = payload.get("last_verified")
        verified_on = date.fromisoformat(verified) if verified else None

        created = updated = 0
        with transaction.atomic():
            for entry in entries:
                _, was_created = MobileNumberAllocation.objects.update_or_create(
                    ndc=entry["ndc"],
                    defaults={
                        "operator": entry.get("operator", "OTHER"),
                        "nsn_length": entry.get("nsn_length", 10),
                        "status": entry.get("status", "ASSIGNED"),
                        "allows_user_accounts": entry.get("allows_user_accounts", True),
                        "notes": entry.get("notes", ""),
                        "source_url": source_url,
                        "last_verified_at": verified_on,
                    },
                )
                created += was_created
                updated += not was_created

            pruned = 0
            if options["prune"]:
                pruned = (
                    MobileNumberAllocation.objects.exclude(ndc__in=[e["ndc"] for e in entries])
                    .exclude(status="WITHDRAWN")
                    .update(status="WITHDRAWN")
                )

        invalidate_allocation_cache()
        self.stdout.write(
            self.style.SUCCESS(
                f"NCC table v{payload.get('version', '?')}: "
                f"{created} created, {updated} updated, {pruned} withdrawn."
            )
        )
