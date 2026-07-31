"""The fee categories the client offers are the ones the server accepts.

Two halves, because the client reads the list both ways. It fetches
`/finance/fee-items/categories/` when it can — one call per screen, cached for
the session — and falls back to the hand-written copy of `FeeCategory` in
`mobile/lib/src/features/fees_screen.dart` when that call fails, which is the
offline case and the case of an old build against a new server.

So the endpoint has to serve every choice, and the fallback has to stay in
step: a category added here and not there is simply never selectable offline,
and an adjustment that should have been TRANSPORT is filed as OTHER for a year
before anyone notices the report. The Dart is read with a regex rather than
parsed — the constant changes about once a release.
"""

import re
from pathlib import Path

import pytest

from apps.accounts.models import Role, User
from apps.finance.models import FeeCategory
from apps.tenants.db import schema_context

DART = (
    Path(__file__).resolve().parents[3]
    / "mobile"
    / "lib"
    / "src"
    / "features"
    / "fees_screen.dart"
)


def test_the_flutter_picker_offers_every_category():
    if not DART.exists():
        pytest.skip("the Flutter client is not checked out here")
    block = re.search(r"const feeCategories = \{(.*?)\};", DART.read_text("utf-8"), re.S)
    assert block, "feeCategories has been renamed or removed in fees_screen.dart"
    offered = set(re.findall(r"'([A-Z_]+)':", block.group(1)))
    assert offered == set(FeeCategory.values)


@pytest.mark.django_db(transaction=True)
@pytest.mark.db_required
def test_the_endpoint_serves_every_category(client, make_tenant, ncc_table):
    from apps.auth_phone import tokens

    school = make_tenant("st-marys")
    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348032222222", first_name="Bursar")
        user.roles.add(Role.objects.get(code="school_admin"))
        pair = tokens.issue_for_user(user, tenant=school)

    response = client.get(
        "/api/v1/finance/fee-items/categories/",
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_AUTHORIZATION=f"Bearer {pair['access']}",
    )

    assert response.status_code == 200
    assert [row["value"] for row in response.json()] == list(FeeCategory.values)
