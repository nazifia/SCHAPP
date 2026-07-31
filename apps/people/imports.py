"""Bulk student intake from a CSV.

Every school arrives with a spreadsheet. Typing four hundred pupils into a
form is how a rollout stalls, so the office uploads what it already has and
the same services that back the API create the records — no second code path
that skips the numbering rules, the guardian matching or the capacity check.

Two decisions worth keeping:

* **The whole file is one transaction.** A partial import is the worst
  outcome: nobody can tell which two hundred rows landed, and re-uploading
  duplicates them. A file that fails is a file the office edits and sends
  again. This is also why there is no dry-run flag — a failed import *is* the
  dry run, and it writes nothing.
* **Every error names its row.** `errors[3].index` is the spreadsheet line,
  so the office fixes line 5 of their file rather than reading "invalid data".

Columns are matched by header name, case- and space-insensitive, because the
file comes from Excel and someone will type "First Name". Unknown columns are
ignored: schools carry their own extra columns and refusing the upload over
one of them helps nobody.
"""

import csv
import io
import logging
from datetime import date, datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError

from apps.api.bulk import BulkOutcome, RowError, atomic_bulk
from apps.api.exceptions import AppError
from apps.numbering.msisdn import InvalidMsisdn, normalize
from apps.tenants.db import tenant_atomic

from . import services
from .models import Gender, Relationship, StudentStatus

logger = logging.getLogger(__name__)

REQUIRED = ("first_name", "last_name")

#: Header aliases. The left-hand side is what the code uses.
ALIASES = {
    "surname": "last_name",
    "othernames": "other_name",
    "other_names": "other_name",
    "middle_name": "other_name",
    "sex": "gender",
    "dob": "date_of_birth",
    "birth_date": "date_of_birth",
    "class": "arm",
    "class_arm": "arm",
    "level_code": "level",
    "programme_code": "programme",
    "state": "state_of_origin",
    "parent_name": "guardian_name",
    "parent_phone": "guardian_phone",
    "guardian_relationship": "relationship",
}

#: Straight onto the Student record.
PERSON_FIELDS = (
    "first_name",
    "last_name",
    "other_name",
    "email",
    "address",
    "state_of_origin",
    "lga",
    "religion",
    "blood_group",
    "admission_number",
)

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y")


class StudentImportError(AppError):
    default_code = "IMPORT_ERROR"


def normalise_header(name: str) -> str:
    key = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(key, key)


def parse_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise StudentImportError(
        f"{value!r} is not a date the importer recognises.",
        code="BAD_DATE",
        details={"accepted": list(DATE_FORMATS)},
    )


def parse_gender(value: str) -> str:
    value = value.strip().upper()
    if not value:
        return ""
    if value[0] in {"M", "F"}:
        return Gender.MALE if value[0] == "M" else Gender.FEMALE
    raise StudentImportError(f"{value!r} is not a gender.", code="BAD_GENDER")


def read_rows(payload) -> list[dict]:
    """Decode an uploaded file into dictionaries keyed by normalised header."""
    if isinstance(payload, bytes):
        text = payload.decode("utf-8-sig", errors="replace")
    elif isinstance(payload, str):
        text = payload
    else:
        text = payload.read()
        if isinstance(text, bytes):
            # utf-8-sig: Excel writes a byte-order mark and the first header
            # would otherwise be "﻿first_name" and match nothing.
            text = text.decode("utf-8-sig", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise StudentImportError("The file has no header row.", code="EMPTY_FILE")

    headers = [normalise_header(name) for name in reader.fieldnames]
    missing = [field for field in REQUIRED if field not in headers]
    if missing:
        raise StudentImportError(
            f"The file is missing required column(s): {', '.join(missing)}.",
            code="MISSING_COLUMNS",
            details={"required": list(REQUIRED), "found": headers},
        )

    # `if name` drops DictReader's None key, which is where it puts the values
    # of a row that has more cells than the header has columns.
    return [
        {normalise_header(name): (value or "").strip() for name, value in raw.items() if name}
        for raw in reader
    ]


class _Lookups:
    """Codes to objects, resolved once for the file rather than once per row.

    Four hundred rows in one class would otherwise be four hundred identical
    queries for the same arm.
    """

    def __init__(self):
        from apps.academics.models import ClassArm, ClassLevel, Programme, Stream

        self.levels = {level.code.upper(): level for level in ClassLevel.objects.all()}
        self.streams = {stream.code.upper(): stream for stream in Stream.objects.all()}
        self.programmes = {p.code.upper(): p for p in Programme.objects.all()}
        self.arms = {}
        for arm in ClassArm.objects.select_related("level"):
            # Addressable as "SS2 Gold" or just "Gold" where it is unambiguous.
            self.arms.setdefault(arm.name.upper(), arm)
            self.arms[f"{arm.level.code} {arm.name}".upper()] = arm

    def resolve(self, table: dict, value: str, what: str):
        if not value:
            return None
        found = table.get(value.strip().upper())
        if found is None:
            raise StudentImportError(
                f"No {what} matches {value!r}.",
                code="UNKNOWN_REFERENCE",
                details={"field": what, "value": value, "known": sorted(table)[:20]},
            )
        return found


def _identify(row: dict, index: int) -> str:
    name = " ".join(p for p in (row.get("first_name"), row.get("last_name")) if p)
    return row.get("admission_number") or name or f"row {index}"


def _code_for(exc: Exception) -> str:
    if isinstance(exc, IntegrityError):
        # Almost always the admission number: either twice in the file or
        # already on record. Both mean "this student is already here".
        return "DUPLICATE"
    return "INVALID_ROW"


def _message_for(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return str(exc.detail)
    if isinstance(exc, IntegrityError):
        return "A student with this admission number already exists."
    if isinstance(exc, DjangoValidationError):
        return "; ".join(exc.messages)
    return str(exc)


def import_students(payload, *, session=None, tenant=None, enforce_capacity: bool = True):
    """Create students (and guardians, and enrolments) from a CSV.

    `session` is optional: without it the students are created but not placed
    in a cohort, which is what a school importing last year's alumni wants.
    """
    rows = read_rows(payload)
    if not rows:
        return BulkOutcome()

    def handler(outcome: BulkOutcome) -> None:
        lookups = _Lookups()
        for index, row in enumerate(rows):
            try:
                # A savepoint per row: a duplicate admission number raises
                # IntegrityError, which leaves the connection unusable until
                # something rolls back. Without this the first bad row would
                # take the rest of the file's error reporting with it, and the
                # office would fix one line at a time.
                with tenant_atomic():
                    _import_row(
                        row,
                        lookups=lookups,
                        session=session,
                        tenant=tenant,
                        enforce_capacity=enforce_capacity,
                    )
            except (AppError, IntegrityError, DjangoValidationError) as exc:
                outcome.errors.append(
                    RowError(
                        index=index,
                        identifier=_identify(row, index),
                        code=getattr(exc, "code", None) or _code_for(exc),
                        message=_message_for(exc),
                    )
                )
            else:
                outcome.created += 1

    outcome = atomic_bulk(handler)
    logger.info(
        "student import finished",
        extra={
            "rows": len(rows),
            "students_created": outcome.created,
            "failed_rows": len(outcome.errors),
        },
    )
    return outcome


def _import_row(row, *, lookups, session, tenant, enforce_capacity):
    for field in REQUIRED:
        if not row.get(field):
            raise StudentImportError(
                f"{field.replace('_', ' ').title()} is required.", code="REQUIRED"
            )

    level = lookups.resolve(lookups.levels, row.get("level", ""), "class level")
    arm = lookups.resolve(lookups.arms, row.get("arm", ""), "class")
    programme = lookups.resolve(lookups.programmes, row.get("programme", ""), "programme")
    stream = lookups.resolve(lookups.streams, row.get("stream", ""), "stream")
    if arm is not None and level is None:
        level = arm.level

    phone = row.get("phone", "")
    if phone:
        try:
            phone = normalize(phone)
        except InvalidMsisdn as exc:
            raise StudentImportError(exc.message, code=exc.code) from exc

    student = services.create_student(
        tenant=tenant,
        admission_number=row.get("admission_number", ""),
        **{field: row.get(field, "") for field in PERSON_FIELDS if field != "admission_number"},
        gender=parse_gender(row.get("gender", "")),
        date_of_birth=parse_date(row.get("date_of_birth", "")),
        phone=phone,
        status=StudentStatus.ACTIVE,
        date_admitted=parse_date(row.get("date_admitted", "")) or date.today(),
        current_level=level,
        current_arm=arm,
        programme=programme,
        stream=stream,
    )

    if row.get("guardian_phone"):
        names = row.get("guardian_name", "").split(" ", 1)
        relationship = (row.get("relationship") or Relationship.GUARDIAN).strip().upper()
        services.link_guardian(
            student=student,
            phone=row["guardian_phone"],
            relationship=(
                relationship if relationship in Relationship.values else Relationship.GUARDIAN
            ),
            is_primary=True,
            first_name=names[0] if names[0] else "",
            last_name=names[1] if len(names) > 1 else "",
        )

    if session is not None and level is not None:
        from apps.academics.services import enrol_student

        enrol_student(
            student=student,
            session=session,
            level=level,
            class_arm=arm,
            programme=programme,
            enforce_capacity=enforce_capacity,
        )
    return student
