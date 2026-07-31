from .base import *  # noqa: F403
from .base import BASE_DIR
from .base import REST_FRAMEWORK as _REST_FRAMEWORK
from .base import STORAGES as _STORAGES

DEBUG = False
ALLOWED_HOSTS = ["*"]

# The manifest backend refuses to serve a file that `collectstatic` has not
# hashed, so any test that renders an admin page fails on `{% static %}` unless
# the suite is preceded by a build step. Tests assert on what the page says,
# never on the URL a stylesheet got.
STORAGES = {
    **_STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Tenant databases are created at runtime, so Django's test runner never gets
# to prefix them with `test_`. Keeping them in their own directory is what
# stops a test run from overwriting a developer's tenant data.
TENANT_DB_DIR = BASE_DIR / ".pytest-tenant-databases"

# Tasks run inline so tenant provisioning is assertable without a worker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
SMS_BACKEND = "apps.communication.sms.console.LocMemBackend"
PUSH_BACKEND = "apps.communication.push.console.LocMemPushBackend"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# DRF's own throttles use the cache too; leaving them on makes the rate-limit
# tests fight framework counters instead of exercising ours. The scopes must
# still be *present* — ScopedRateThrottle raises on an unknown scope — so each
# is set to None, which DRF reads as "no limit".
REST_FRAMEWORK = {
    **_REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": dict.fromkeys(_REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]),
}

# Deterministic key so encrypted-field tests do not depend on the environment.
FIELD_ENCRYPTION_KEY = "E1jMh9m6uW1x2FvR3kZq7pT8sYbN0cJdA5gLhQwXeUo="

LOGGING = {"version": 1, "disable_existing_loggers": False, "root": {"level": "CRITICAL"}}
