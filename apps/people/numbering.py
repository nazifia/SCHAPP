"""Admission, matriculation and staff number generation.

Formats are configurable per tenant because every institution has its own
house style and they will not change it for us. Placeholders:

    {yy}     two-digit admission year        25
    {yyyy}   four-digit admission year       2025
    {dept}   department code                 CSC
    {prog}   programme code                  ND-CSC
    {level}  level code                      100
    {serial} zero-padded running number      0042

Examples: `CSC/25/0042`, `KC/2025/0042`, `{prog}/{yy}/{serial}`.
"""

import re
from dataclasses import dataclass

from apps.tenants.db import tenant_atomic

DEFAULT_ADMISSION_FORMAT = "{yy}/{serial}"
DEFAULT_MATRIC_FORMAT = "{dept}/{yy}/{serial}"
DEFAULT_STAFF_FORMAT = "STF/{yy}/{serial}"
SERIAL_WIDTH = 4

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class NumberFormatError(ValueError):
    pass


@dataclass
class NumberContext:
    year: int
    dept: str = ""
    prog: str = ""
    level: str = ""


def render(fmt: str, context: NumberContext, serial: int) -> str:
    values = {
        "yy": f"{context.year % 100:02d}",
        "yyyy": str(context.year),
        "dept": context.dept,
        "prog": context.prog,
        "level": context.level,
        "serial": f"{serial:0{SERIAL_WIDTH}d}",
    }
    used = _PLACEHOLDER.findall(fmt)
    unknown = [m for m in used if m not in values]
    if unknown:
        raise NumberFormatError(f"Unknown placeholder(s) in format: {unknown}")
    # An empty placeholder collapses silently: `{dept}/{yy}/{serial}` on a
    # student with no programme rendered `/26/0001` and, because a matriculation
    # number is issued once and printed on the transcript, that stayed wrong
    # for ever. Refuse instead — the number is missing data, not short.
    empty = [m for m in used if m != "serial" and not values[m]]
    if empty:
        raise NumberFormatError(f"Format {fmt!r} needs {empty}, which this record does not have.")
    return fmt.format(**values)


def prefix_of(fmt: str, context: NumberContext) -> str:
    """Everything before `{serial}` — the group a serial counts within.

    `CSC/25/0042` and `CSC/25/0043` share the prefix `CSC/25/`, so serials
    restart per department per year rather than running globally.
    """
    head = fmt.split("{serial}")[0]
    return render(head + "{serial}", context, 0)[:-SERIAL_WIDTH]


@tenant_atomic()
def next_number(model, field: str, fmt: str, context: NumberContext) -> str:
    """Allocate the next number for `model.field`.

    Derives the serial from the highest existing value with the same prefix,
    under a row lock on that highest value.

    The lock is why this reads the top row rather than asking for
    ``Max(field)``: ``Query.get_aggregation`` sets ``select_for_update = False``
    unconditionally, so an aggregate never emits ``FOR UPDATE`` on any backend
    and the lock this promised was never taken. SQLite hid it in development —
    ``transaction_mode=IMMEDIATE`` holds the whole database for the length of
    the transaction — while MySQL let two concurrent allocations read the same
    maximum, render the same number and collide on the unique index, which the
    bursar taking cash at the counter sees as a 500. Ordering and taking one
    row keeps the lock.

    Ordering by the string is the same answer ``Max`` gave: the serial is
    zero-padded to a fixed width, so within one prefix the collation order and
    the numeric order agree (both alike once a serial outgrows SERIAL_WIDTH).

    ponytail: max+1 under a lock, not a counter table. Two concurrent
    admissions in the same department and year serialise on that lock; if bulk
    import ever makes that hurt, add a per-prefix sequence table. The one gap
    left is the *first* number in a prefix, where there is no row to lock —
    close that by retrying the insert on IntegrityError at the call site if it
    is ever seen.
    """
    prefix = prefix_of(fmt, context)
    existing = (
        model.objects.select_for_update()
        .filter(**{f"{field}__startswith": prefix})
        .order_by(f"-{field}")
        .values_list(field, flat=True)
        .first()
    )

    serial = 1
    if existing:
        tail = existing[len(prefix) :]
        digits = re.match(r"\d+", tail)
        if digits:
            serial = int(digits.group()) + 1

    return render(fmt, context, serial)
