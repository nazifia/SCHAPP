import uuid

from django.db import models


class TimeStampedModel(models.Model):
    """UUID primary key + audit timestamps.

    UUIDs because IDs travel across tenant boundaries in exports, QR codes and
    deep links; sequential integers would leak row counts and invite
    enumeration across schemas.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def delete(self):
        """Soft. `hard_delete()` is the way to actually remove rows.

        Django's cascade collector does not route through here — it builds its
        own `DeleteQuery` — so this only intercepts deletes the application
        asks for by name, which is exactly the set that should be soft.
        """
        from django.utils import timezone

        return self.alive().update(deleted_at=timezone.now(), updated_at=timezone.now())

    def hard_delete(self):
        return super().delete()


class SoftDeleteModel(TimeStampedModel):
    """Rows schools must never truly lose (students, invoices, results).

    `delete()` is overridden to mean `soft_delete()`, and that is not belt and
    braces — it was the whole gap. `soft_delete()` existed from Phase 1 and no
    application code ever called it, while `StudentViewSet` and `StaffViewSet`
    were plain `ModelViewSet`s: `DELETE /people/students/{id}/` reached
    `ModelViewSet.destroy`, which calls `instance.delete()`, which removed the
    row for good — along with, by cascade, every enrolment, attendance record,
    registration, result and guardian link attached to it. The docstring above
    was the only thing saying otherwise.

    `hard_delete()` is the deliberate way out, for the two callers that mean
    it: a data-protection erasure and a test tearing down its own fixtures.

    One path is not covered and cannot be: the Django admin's bulk
    `delete_selected` builds a `Collector` and never calls `Model.delete()`.
    That is the deliberate erasure route for platform staff on `Student` and
    `Staff`; `InvoiceAdmin` and `PaymentAdmin` close it explicitly, because
    "money is never deleted" admits no escape hatch.
    """

    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = SoftDeleteQuerySet.as_manager()

    class Meta(TimeStampedModel.Meta):
        abstract = True

    def soft_delete(self) -> None:
        from django.utils import timezone

        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])

    def delete(self, *args, **kwargs):
        self.soft_delete()
        # DRF and the admin ignore this, but Django's own contract is
        # `(count, {label: count})` and something will one day read it.
        return (1, {self._meta.label: 1})

    def hard_delete(self, *args, **kwargs):
        return super().delete(*args, **kwargs)
