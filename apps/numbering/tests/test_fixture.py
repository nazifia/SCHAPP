"""The shipped NCC fixture, checked against the model and against itself.

Transcribed from the NCC Mobile Number Allocation Table on 2026-07-25. These
tests are the guard rail: a future edit that fat-fingers an operator code or
silently reverts a corrected row fails here rather than in production, where
the symptom is a school unable to log in.
"""

import json
from pathlib import Path

import pytest

from apps.numbering.models import AllocationStatus, Operator

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ncc_mobile_allocations.json"


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def by_ndc(fixture) -> dict:
    return {entry["ndc"]: entry for entry in fixture["entries"]}


def test_it_cites_its_source_and_a_verification_date(fixture):
    assert "ncc.gov.ng" in fixture["source_url"]
    assert fixture["last_verified"]
    assert fixture["version"]


def test_every_operator_code_is_one_the_model_accepts(fixture):
    unknown = {
        entry["operator"]
        for entry in fixture["entries"]
        if entry["operator"] not in Operator.values
    }
    assert not unknown, f"fixture uses operator codes the model rejects: {unknown}"


def test_every_status_is_one_the_model_accepts(fixture):
    unknown = {
        entry.get("status", "ASSIGNED")
        for entry in fixture["entries"]
        if entry.get("status", "ASSIGNED") not in AllocationStatus.values
    }
    assert not unknown


def test_ndcs_are_unique_and_well_formed(fixture):
    ndcs = [entry["ndc"] for entry in fixture["entries"]]
    assert len(ndcs) == len(set(ndcs))
    for ndc in ndcs:
        assert ndc.startswith("0"), ndc
        assert ndc.isdigit(), ndc
        assert 4 <= len(ndc) <= 5, ndc


@pytest.mark.parametrize(
    ("ndc", "operator"),
    [
        # Rows corrected against the live NCC table on 2026-07-25. The first
        # four were wrong in the initial hand-assembled fixture.
        ("0704", "MTN"),
        ("0707", "MTN"),
        ("0801", "MAFAB"),
        ("0804", "MTEL"),
    ],
)
def test_corrected_operator_rows_stay_corrected(by_ndc, ndc, operator):
    assert by_ndc[ndc]["operator"] == operator


@pytest.mark.parametrize("ndc", ["0709", "0819"])
def test_withdrawn_blocks_stay_withdrawn(by_ndc, ndc):
    assert by_ndc[ndc]["status"] == "WITHDRAWN"


@pytest.mark.parametrize("ndc", ["0700", "0800", "0900"])
def test_shared_and_reserved_blocks_can_never_be_a_login(by_ndc, ndc):
    assert by_ndc[ndc]["allows_user_accounts"] is False


def test_the_four_gsm_operators_all_hold_blocks(by_ndc):
    holders = {entry["operator"] for entry in by_ndc.values() if entry["status"] == "ASSIGNED"}
    assert {"MTN", "GLO", "AIRTEL", "EMTS"} <= holders


def test_unallocated_ndcs_are_absent_rather_than_listed_as_available(by_ndc):
    """0910, 0914, 0917-0919 are not in the NCC table; absence is the answer."""
    for ndc in ("0910", "0914", "0917", "0918"):
        assert ndc not in by_ndc
