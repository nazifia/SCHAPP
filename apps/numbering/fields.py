"""PhoneNumber value-object field.

One canonical representation in the database, always E.164. Normalisation
happens in the field so a raw `08031234567` can never reach a row by any code
path — API, admin, management command or import script.
"""

from django.core.exceptions import ValidationError
from django.db import models

from .msisdn import InvalidMsisdn, format_display, normalize


class PhoneNumberField(models.CharField):
    """CharField that stores canonical E.164 and refuses anything else."""

    description = "Nigerian MSISDN stored as E.164"

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 16)
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if kwargs.get("max_length") == 16:
            del kwargs["max_length"]
        return name, path, args, kwargs

    def get_prep_value(self, value):
        if value in (None, ""):
            return value
        try:
            return normalize(value)
        except InvalidMsisdn as exc:
            raise ValidationError(exc.message, code=exc.code) from exc

    def to_python(self, value):
        if value in (None, ""):
            return value
        try:
            return normalize(value)
        except InvalidMsisdn as exc:
            raise ValidationError(exc.message, code=exc.code) from exc


def display_variant(e164: str) -> str:
    """Companion for the `*_display` column kept alongside each phone field."""
    return format_display(e164) if e164 else ""
