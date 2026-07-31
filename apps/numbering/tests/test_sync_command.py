"""The allocation table is data, and this proves the data path works."""

import json

import pytest
from django.core.management import call_command

from apps.numbering.models import MobileNumberAllocation
from apps.numbering.msisdn import InvalidMsisdn, normalize
from apps.numbering.selectors import invalidate_allocation_cache

pytestmark = [pytest.mark.django_db, pytest.mark.db_required]


def test_the_shipped_fixture_loads():
    call_command("sync_ncc_allocations", verbosity=0)
    assert MobileNumberAllocation.objects.count() > 30
    assert MobileNumberAllocation.objects.get(ndc="0803").operator == "MTN"
    assert not MobileNumberAllocation.objects.get(ndc="0700").allows_user_accounts
    assert MobileNumberAllocation.objects.get(ndc="0709").status == "WITHDRAWN"
    assert MobileNumberAllocation.objects.get(ndc="0803").source_url.startswith("https://")
    assert MobileNumberAllocation.objects.get(ndc="0803").last_verified_at is not None


def test_running_it_twice_changes_nothing():
    call_command("sync_ncc_allocations", verbosity=0)
    before = MobileNumberAllocation.objects.count()
    call_command("sync_ncc_allocations", verbosity=0)
    assert MobileNumberAllocation.objects.count() == before


def test_a_new_prefix_is_a_data_change_only(tmp_path):
    """Acceptance criterion: no code change, no deploy, no migration."""
    call_command("sync_ncc_allocations", verbosity=0)
    invalidate_allocation_cache()

    with pytest.raises(InvalidMsisdn):
        normalize("09201234567")

    updated = tmp_path / "ncc.json"
    updated.write_text(
        json.dumps(
            {
                "version": "2026-02",
                "source_url": "https://www.ncc.gov.ng/operators/mobile-number-allocation-table",
                "last_verified": "2026-02-01",
                "entries": [{"ndc": "0920", "operator": "MTN", "status": "ASSIGNED"}],
            }
        ),
        encoding="utf-8",
    )
    call_command("sync_ncc_allocations", file=str(updated), verbosity=0)

    assert normalize("09201234567") == "+2349201234567"


def test_prune_withdraws_prefixes_missing_from_the_fixture(tmp_path):
    call_command("sync_ncc_allocations", verbosity=0)
    minimal = tmp_path / "ncc.json"
    minimal.write_text(
        json.dumps(
            {"version": "x", "entries": [{"ndc": "0803", "operator": "MTN", "status": "ASSIGNED"}]}
        ),
        encoding="utf-8",
    )
    call_command("sync_ncc_allocations", file=str(minimal), prune=True, verbosity=0)

    assert MobileNumberAllocation.objects.get(ndc="0803").status == "ASSIGNED"
    assert MobileNumberAllocation.objects.get(ndc="0806").status == "WITHDRAWN"


def test_the_cache_is_dropped_after_a_sync():
    call_command("sync_ncc_allocations", verbosity=0)
    assert normalize("08031234567") == "+2348031234567"

    MobileNumberAllocation.objects.filter(ndc="0803").update(status="WITHDRAWN")
    invalidate_allocation_cache()

    with pytest.raises(InvalidMsisdn):
        normalize("08031234567")
