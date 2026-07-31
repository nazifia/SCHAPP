"""Symmetric encryption for secrets we must be able to read back.

Used for payment-gateway keys, SMS credentials and NINs. Hashing is not an
option for these: we have to send the real value to Paystack/Termii.
"""

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

logger = logging.getLogger(__name__)

PREFIX = "enc:"


class _Unreadable(str):
    """A secret the current key cannot decrypt.

    It *is* the empty string — falsy, equal to `""`, and every caller that
    already treats empty as "unconfigured" keeps working untouched. What it
    adds is identity: the field can tell "there is no secret here" from
    "there is a secret here and this key is the wrong one", which the plain
    `""` could not, and that difference is what stops the value being written
    back over the ciphertext it failed to read.
    """

    __slots__ = ()


#: Singleton, so `value is UNREADABLE` is the test.
UNREADABLE = _Unreadable()


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", "")
    if not key:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEY is unset. Generate one with "
            'python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


class EncryptedTextField(models.TextField):
    """Stores ciphertext, hands back plaintext.

    Values already stored in cleartext (e.g. rows predating the field switch)
    are returned as-is rather than raising, so a migration can encrypt them
    lazily.

    A value the key cannot decrypt reads as empty — surfacing nothing beats a
    crash deep in a serializer — but it is `UNREADABLE` rather than `""`, and
    `pre_save` refuses to persist that. Without the distinction there was a
    quiet path to permanent loss: deploy with the wrong key (a rotation, a
    backup restored into the wrong environment, staging credentials in
    production), have anyone edit one unrelated field on `TenantConfiguration`,
    and `Model.save()` writes *every* column — replacing each gateway secret,
    SMS credential and NIN with a blank that fixing the key afterwards cannot
    undo.
    """

    def get_prep_value(self, value):
        if value in (None, ""):
            return value
        return PREFIX + _fernet().encrypt(str(value).encode()).decode()

    def pre_save(self, model_instance, add):
        value = getattr(model_instance, self.attname)
        if value is UNREADABLE:
            raise ImproperlyConfigured(
                f"Refusing to save {model_instance._meta.label}.{self.attname}: its stored "
                "value could not be decrypted with the current FIELD_ENCRYPTION_KEY, and "
                "writing it back would destroy the ciphertext. Restore the correct key, or "
                "set a new value for this field explicitly."
            )
        return value

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        if not value.startswith(PREFIX):
            return value
        try:
            return _fernet().decrypt(value[len(PREFIX) :].encode()).decode()
        except InvalidToken:
            # Loudly: a wrong key used to produce no signal anywhere at all,
            # so the first symptom was a school's payments quietly failing.
            logger.error(
                "could not decrypt an encrypted field with the current key",
                # `attname` only exists once the field is bound to a model, and
                # a log line must never be the thing that raises.
                extra={"encrypted_field": getattr(self, "attname", "")},
            )
            return UNREADABLE


def mask_tail(value: str | None, keep: int = 4) -> str:
    """`12345678912` -> `•••••••8912`. For NIN/account numbers in detail views."""
    if not value:
        return ""
    tail = value[-keep:]
    return "•" * max(len(value) - keep, 0) + tail
