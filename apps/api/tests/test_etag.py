"""An ETag has to identify the representation, not just the queryset.

`_compute_etag` aggregates `(count, max(updated_at))` over the *filtered*
queryset, and nothing in that changes with `?cursor=`, `?expand=` or
`?page_size=` — so every page of a list carried one tag. A client keying its
cache on the path, which is what the offline sync does, asks for page two with
page one's tag and is answered 304 with no body.

No database: the aggregate is stubbed, because what is under test is what the
tag is built from.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from apps.api.mixins import ETagMixin

STATS = {"n": 120, "latest": datetime(2026, 7, 29, 9, 0, tzinfo=UTC)}


def _etag_for(query_string: str) -> str:
    view = ETagMixin()
    view.request = SimpleNamespace(META={"QUERY_STRING": query_string})
    return view._compute_etag(SimpleNamespace(aggregate=lambda **kwargs: STATS))


def test_two_pages_of_one_list_do_not_share_a_tag():
    assert _etag_for("") != _etag_for("cursor=cD0yMDI2LTA3LTI5")


def test_expanding_a_list_changes_its_tag():
    assert _etag_for("term=abc") != _etag_for("term=abc&expand=student")


def test_the_same_request_is_still_cacheable():
    assert _etag_for("term=abc&page_size=50") == _etag_for("term=abc&page_size=50")


def test_a_changed_queryset_still_changes_the_tag():
    """The original property, unbroken: the counts still drive the tag."""
    view = ETagMixin()
    view.request = SimpleNamespace(META={"QUERY_STRING": ""})
    first = view._compute_etag(SimpleNamespace(aggregate=lambda **kwargs: STATS))
    second = view._compute_etag(SimpleNamespace(aggregate=lambda **kwargs: {**STATS, "n": 121}))
    assert first != second
