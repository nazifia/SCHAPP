"""Signing a platform superuser in on a school's own address.

`ModelBackend` resolves the user through the router, which inside a tenant is
that school's `accounts_user` table — the platform's staff are simply not in
it, so `<school>.<domain>/admin/` refuses them. This backend answers for that
one case: it reads the platform database, and returns nobody who is not an
active superuser.

Django records which backend authenticated a session and calls that same
backend on every later request, so `get_user` is what keeps the superuser
signed in while a school is selected. It re-checks `is_superuser` for the same
reason `AdminTenantSwitchMiddleware` does: a demotion should end the session at
the next request, not at the next login.

Ordering matters — this is listed *after* `ModelBackend` so a school's own user
always answers for their own school first.
"""

from django.contrib.auth.backends import ModelBackend

from apps.tenants.db import schema_context


class PlatformSuperuserBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        with schema_context(None):
            user = super().authenticate(request, username=username, password=password, **kwargs)
            return user if user is not None and user.is_superuser else None

    def get_user(self, user_id):
        with schema_context(None):
            user = super().get_user(user_id)
            return user if user is not None and user.is_superuser else None
