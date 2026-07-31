"""Bulk write support.

A teacher submitting a class of forty must send **one** request. Two rules:

* the whole batch is one transaction — a half-saved mark sheet is worse than
  a rejected one;
* every row reports its own outcome, so the app can highlight the three rows
  that failed instead of showing "error".
"""

from dataclasses import dataclass, field

from rest_framework import status
from rest_framework.response import Response

from apps.tenants.db import tenant_atomic


@dataclass
class RowError:
    index: int
    identifier: str
    code: str
    message: str


@dataclass
class BulkOutcome:
    created: int = 0
    updated: int = 0
    #: Rows the batch removed — an unmark, not a correction.
    deleted: int = 0
    errors: list[RowError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_response(self) -> Response:
        body = {
            "created": self.created,
            "updated": self.updated,
            "deleted": self.deleted,
            "errors": [
                {
                    "index": e.index,
                    "identifier": e.identifier,
                    "code": e.code,
                    "message": e.message,
                }
                for e in self.errors
            ],
        }
        # 422: the request was well-formed but some rows could not be applied.
        # The transaction rolled back, so nothing was written.
        return Response(
            body, status=status.HTTP_200_OK if self.ok else status.HTTP_422_UNPROCESSABLE_ENTITY
        )


def atomic_bulk(handler):
    """Run `handler()` in a transaction, rolling back if any row failed."""
    outcome = BulkOutcome()
    try:
        with tenant_atomic():
            handler(outcome)
            if outcome.errors:
                raise _Rollback
    except _Rollback:
        outcome.created = outcome.updated = outcome.deleted = 0
    return outcome


class _Rollback(Exception):
    pass
