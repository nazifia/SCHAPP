"""JWT authentication that knows which database holds the user.

simplejwt resolves the id in a token through the router, which inside a school
is that school's `accounts_user` table. Two things follow from that, and this
class is both of them:

* a platform superuser has no row in any school, so their token names a row in
  the platform database — tokens minted for them carry ``platform: true`` and
  are resolved there instead, re-checking `is_superuser` every time;
* user ids are per database, so id 7 is a *different person* in every school.
  A token that never named a tenant must therefore not be spent inside one:
  without this check it would authenticate as whoever happens to hold that id
  in the school named by the header.
"""

from django.utils.translation import gettext_lazy as _
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.settings import api_settings

from apps.accounts.platform import PLATFORM_CLAIM, platform_superuser
from apps.tenants.db import current_schema, is_public

from .tokens import TENANT_SLUG_CLAIM


class PlatformAwareJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        if validated_token.get(PLATFORM_CLAIM):
            user = platform_superuser(pk=validated_token.get(api_settings.USER_ID_CLAIM))
            if user is None:
                raise AuthenticationFailed(
                    _("No active platform account for this session."), code="user_not_found"
                )
            return user

        if not validated_token.get(TENANT_SLUG_CLAIM) and not is_public(current_schema()):
            raise AuthenticationFailed(
                _("This session was not issued for this institution."), code="wrong_tenant"
            )

        return super().get_user(validated_token)
