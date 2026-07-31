"""Platform superusers signing in to a school they have no row in.

`apps.accounts` is listed in both halves of the app split, so every school has
its own `accounts_user` table and the platform's own staff appear in none of
them. Left alone that means a superuser can only sign in on the platform host:
the admin on a school's own address and every API login carry a tenant, and the
lookup lands in that school's table where they do not exist.

The rule here is one sentence: **a platform superuser is authenticated against
the platform database, and everything their session owns is written there
too.** Devices, token families, blacklisted refresh tokens and OTP challenges
all hold a foreign key to the user, and a school's database cannot store a key
that points into another one. Only the *tenant* data they then read and write
follows the selected school.

Nothing is copied into a school, which is what keeps revocation immediate:
demote or deactivate the account on the platform and the next request resolves
the same row and refuses. There is no mirrored god-account left behind in
fifty databases to find and delete.

See `apps.tenants.admin_switch` for the same reach from the platform admin.
"""

from __future__ import annotations

from contextlib import nullcontext

from django.db import DEFAULT_DB_ALIAS

from apps.tenants.db import schema_context

#: Stamped on tokens minted for a platform superuser. It says which database
#: holds the user and nothing else — it grants nothing, and every request
#: re-reads `is_superuser` from that row before honouring it.
PLATFORM_CLAIM = "platform"


def is_platform_user(user) -> bool:
    """A superuser whose row was read from the platform database.

    `_state.db` is set by the queryset that loaded the row, so this stays true
    for the life of the object even after a school has been selected — which is
    exactly the window in which the distinction matters.
    """
    return bool(
        getattr(user, "is_superuser", False)
        and getattr(getattr(user, "_state", None), "db", None) == DEFAULT_DB_ALIAS
    )


def platform_superuser(*, phone: str = "", pk=None):
    """The active platform superuser with this phone (or id), or None."""
    from apps.accounts.models import User

    if not phone and pk is None:
        return None
    with schema_context(None):
        lookup = {"phone": phone} if phone else {"pk": pk}
        return User.objects.filter(is_active=True, is_superuser=True, **lookup).first()


def platform_context(user):
    """Select the database that owns this user's session rows.

    A no-op for a school's own users; the platform database for a superuser
    reaching in from outside.
    """
    return schema_context(None) if is_platform_user(user) else nullcontext()
