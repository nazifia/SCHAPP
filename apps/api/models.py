"""One table, for one promise: a replayed write is not a second write.

Lives in `apps.api`, which is listed in both SHARED_APPS and TENANT_APPS, so a
school's keys sit in that school's database and the platform's sit in the
platform's — the same split every other both-listed app uses.
"""

from django.db import models

from apps.common.models import TimeStampedModel


class IdempotencyRecord(TimeStampedModel):
    """A claimed key, and the response it eventually produced.

    The row is created *before* the view runs and deleted again if the view
    does not answer 2xx, so a failed write leaves the key free rather than
    burning it. Between those two points the row exists with `completed_at`
    unset, which is what makes a concurrent duplicate answer 409 instead of
    running the write a second time.
    """

    #: 190, not 255: utf8mb4 makes a 255-character unique index 1020 bytes,
    #: past the 767 an older InnoDB row format allows. Same family of trap as
    #: the conditional UniqueConstraint MySQL drops on the floor.
    key = models.CharField(max_length=190, unique=True)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=500)

    #: SHA-256 of the request body. A key sent again with a *different* body is
    #: a client bug, not a retry, and answering it with the first response
    #: would silently discard the second write.
    request_fingerprint = models.CharField(max_length=64)

    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        verbose_name = "idempotency record"

    def __str__(self) -> str:
        return f"{self.method} {self.path} [{self.key}]"
