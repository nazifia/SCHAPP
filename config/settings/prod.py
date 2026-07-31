from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import DATABASES, env

DEBUG = False
SERVE_API_DOCS = False

SECRET_KEY = env("DJANGO_SECRET_KEY")  # no default: fail fast if unset
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# Development runs on SQLite, which enforces neither column lengths nor foreign
# keys the way MySQL does. Falling back to it in production would turn every
# constraint this app relies on into a suggestion.
if "mysql" not in DATABASES["default"]["ENGINE"]:
    raise ImproperlyConfigured(
        "Production runs on MySQL. Set DATABASE_URL=mysql://user:pass@host:3306/schapp "
        f"(got ENGINE={DATABASES['default']['ENGINE']!r})."
    )

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
X_FRAME_OPTIONS = "DENY"

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = True

_sentry_dsn = env("SENTRY_DSN", default="")
if _sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[DjangoIntegration()],
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.1),
        send_default_pii=False,  # NDPR: never ship PII to a third party by default
        environment=env("ENVIRONMENT", default="production"),
    )
