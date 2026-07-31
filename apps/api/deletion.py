"""Refuse to delete a row that other records hang off.

Reference data is the dangerous kind to delete. A session, a class level, a
term, an assessment component: each is one row, and each has an academic year
of history pointing at it with `on_delete=CASCADE`. `DELETE
/academics/sessions/{id}/` took the terms, and with them every subject
registration, score, subject result, term result, attendance record and
enrolment for that year — one request, no confirmation, nothing left to
restore from. Where a bill happened to exist the same request raised
`ProtectedError` instead, because `Invoice.session` is `PROTECT`: an unhandled
500 on the identical action, decided by whether the bursar had run invoicing
yet.

The rest of the codebase works hard at this — students and invoices are
soft-deleted, the audit trail is append-only, a transcript is verifiable years
later. None of that survives its academic session being deleted.

So: ask Django what a delete would actually take, and refuse if the answer is
anything more than the row itself. `SET_NULL` dependents are not counted —
nulling `ClassArm.stream` loses nothing and is a legitimate edit.
"""

from django.db.models.deletion import Collector, ProtectedError
from rest_framework import status

from apps.api.exceptions import AppError


class DependentsExist(AppError):
    """Something points at this row, so deleting it would take that too."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "DEPENDENTS_EXIST"


def dependents_of(instance) -> dict[str, int]:
    """`{"subject registrations": 240, ...}` — what a delete would destroy.

    Empty when the row stands alone. `PROTECT` is reported the same way as
    `CASCADE`: from the caller's side both mean "this is still in use", and
    the difference between losing the data and being refused is not something
    they should have to know to read the error.
    """
    collector = Collector(using=instance._state.db)
    try:
        collector.collect([instance])
    except ProtectedError as exc:
        return _label_counts(exc.protected_objects)

    counts: dict[str, int] = {}
    for model, objects in collector.data.items():
        # The instance itself is in here; everything else is collateral.
        extra = len(objects) - (1 if model is type(instance) else 0)
        if extra > 0:
            _add(counts, model, extra)

    # `collector.data` is only half the answer. A related model with nothing
    # cascading below it and no signal receivers is "fast deletable": Django
    # skips loading the rows and files a bare queryset under `fast_deletes`
    # instead. `Score` is exactly that shape, so counting only `data` reported
    # a mark-sheet column with four hundred marks under it as unused.
    for queryset in collector.fast_deletes:
        count = queryset.count()
        if count:
            _add(counts, queryset.model, count)

    # `collector.field_updates` is deliberately not consulted: those are
    # SET_NULL relations, and nulling `ClassArm.stream` loses nothing.
    return counts


def _add(counts: dict[str, int], model, count: int) -> None:
    label = str(model._meta.verbose_name_plural)
    counts[label] = counts.get(label, 0) + count


def _label_counts(objects) -> dict[str, int]:
    counts: dict[str, int] = {}
    for obj in objects:
        label = str(obj._meta.verbose_name_plural)
        counts[label] = counts.get(label, 0) + 1
    return counts


class ProtectDependentsMixin:
    """Turn a destructive cascade into a 409 that names what is in the way."""

    def perform_destroy(self, instance):
        dependents = dependents_of(instance)
        if dependents:
            listed = ", ".join(f"{count} {label}" for label, count in sorted(dependents.items()))
            raise DependentsExist(
                f"{instance} is still in use by {listed}. "
                "Deactivate it instead, or remove those first.",
                details={"dependents": dependents},
            )
        super().perform_destroy(instance)
