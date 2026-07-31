from .base import *  # noqa: F403
from .base import INSTALLED_APPS, MIDDLEWARE, env

DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar"]
MIDDLEWARE = [*MIDDLEWARE, "debug_toolbar.middleware.DebugToolbarMiddleware"]
INTERNAL_IPS = ["127.0.0.1"]
# debug_toolbar's default check calls into request.META in ways that clash with
# the tenant middleware; a plain callable keeps it predictable.
DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": lambda request: DEBUG}

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "localhost"
EMAIL_PORT = 1025

CELERY_TASK_ALWAYS_EAGER = False
CORS_ALLOW_ALL_ORIGINS = True

# Any request_id accepts this code, so the Flutter client can sign in without
# the developer reading the OTP off the runserver console. DEBUG-gated in
# apps.auth_phone.services.verify_otp; set OTP_DEV_CODE="" to disable.
OTP_DEV_CODE = env("OTP_DEV_CODE", default="000000")

# A SQLite dev database needs no server running, and neither should the cache
# or the queue: an unset REDIS_URL means "no infrastructure", not "localhost".
# Set REDIS_URL (docker compose does) to get the real broker and cache back.
if not env("REDIS_URL", default=""):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True
