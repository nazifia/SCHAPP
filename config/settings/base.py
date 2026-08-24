"""Settings shared by every environment.

Anything environment-specific belongs in dev/staging/prod/test, not here.
"""

from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, []),
)
env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-insecure-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# --------------------------------------------------------------------------
# Applications
#
# The apps are split in two: SHARED_APPS live in the platform database (one
# copy for the whole platform), TENANT_APPS are migrated into every tenant
# database (one copy per school). apps.tenants.db.TenantRouter routes on this
# split, so an app's position here is what decides where its tables land.
# --------------------------------------------------------------------------
SHARED_APPS = [
    "apps.tenants",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.admin",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "django_celery_beat",
    "apps.common",
    "apps.api",
    # National numbering plan: one copy for the platform, never per school.
    "apps.numbering",
    # Listed in both: the public copy holds platform staff and platform-level
    # audit, each tenant copy holds that school's users and its own audit.
    "apps.accounts",
    "apps.audit",
    # Listed in both for the same reason as `accounts`: a platform superuser
    # signing in to a school is authenticated against the platform database, so
    # their OTP challenge has to be storable there — `OtpRequest.user` is a
    # foreign key and a school's cannot point out of its own database.
    "apps.auth_phone",
    "rest_framework_simplejwt.token_blacklist",
]

TENANT_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    # Listed in both: the idempotency table holds one school's replayed writes
    # in that school's database, and the platform's in the platform's.
    "apps.api",
    "apps.accounts",
    "apps.auth_phone",
    "apps.audit",
    "rest_framework_simplejwt.token_blacklist",
    "apps.people",
    "apps.academics",
    "apps.attendance",
    "apps.assessment",
    "apps.finance",
    # Tenant-side: announcements and the message log are one school's own.
    # The SMS/push *backends* in this app are stateless and used from anywhere.
    "apps.communication",
    # Later: apps.library, apps.hostel
]

AUTH_USER_MODEL = "accounts.User"

INSTALLED_APPS = SHARED_APPS + [a for a in TENANT_APPS if a not in SHARED_APPS]

# --------------------------------------------------------------------------
# Multi-tenancy
#
# One database per school. `PUBLIC_SCHEMA_NAME` names the platform tenant,
# whose database is the `default` connection; every other tenant gets its own.
# --------------------------------------------------------------------------
PUBLIC_SCHEMA_NAME = "public"

TENANT_HEADER = "X-Tenant-Slug"
BASE_DOMAIN = env("BASE_DOMAIN", default="localhost")

# Paths served from the public schema without a tenant. Everything else
# requires a resolved tenant or gets TENANT_NOT_FOUND.
PUBLIC_URL_PREFIXES = [
    "/healthz",
    "/readyz",
    "/admin/",
    "/api/schema",
    "/api/docs",
    "/api/v1/public/",
    "/static/",
    "/__debug__/",
]

# The origin printed into a document's verification QR. Not derived from the
# request: a transcript is verified years after the connection that produced
# it, and a school's own subdomain is not where the check lives.
DOCUMENT_VERIFY_BASE_URL = env(
    "DOCUMENT_VERIFY_BASE_URL",
    default=("http://localhost:8000" if BASE_DOMAIN == "localhost" else f"https://{BASE_DOMAIN}"),
)

#: How many proxies of ours stand in front of Django. `apps.audit.client_ip`
#: and DRF's throttles read the `X-Forwarded-For` entry this many places from
#: the right; everything to its left is the client's to invent. 0 — Django
#: addressed directly — ignores the header, which is the safe default: a
#: deployment that trusts a header it does not in fact terminate lets any
#: caller pick their own identity for the per-IP OTP limit and the audit trail.
TRUSTED_PROXY_DEPTH = env.int("TRUSTED_PROXY_DEPTH", default=0)

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    # Must precede authentication: the schema has to be set before any
    # queryset (including the session/user lookup) is evaluated.
    "apps.tenants.middleware.TenantResolutionMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # Must follow authentication: the platform superuser is looked up in the
    # platform database, and this selects a different one. After messages too,
    # so it can explain a missing selection instead of leaving a bare 403.
    "apps.tenants.middleware.AdminTenantSwitchMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Innermost, so a request rejected by CSRF or authentication never burns a
    # key. Needs the tenant already resolved: the records are per school.
    "apps.api.idempotency.IdempotencyMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Feeds the tenant selector in the admin header. Answers with
                # an empty dict for anyone who is not a platform superuser.
                "apps.tenants.admin_switch.admin_tenant",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# Database
#
# SQLite in development, MySQL in production — set DATABASE_URL to choose.
# The default is SQLite so a fresh checkout runs with no server to install;
# config/settings/prod.py refuses to start on anything but MySQL.
# --------------------------------------------------------------------------
_database_url = env("DATABASE_URL", default="")
DATABASES = {
    "default": (
        env.db_url_config(_database_url)
        if _database_url
        # Built by hand rather than from a URL: sqlite:// URLs and Windows
        # drive letters do not survive each other.
        else {"ENGINE": "django.db.backends.sqlite3", "NAME": str(BASE_DIR / "schapp.sqlite3")}
    )
}
DATABASE_ROUTERS = ["apps.tenants.db.TenantRouter"]

#: Where per-tenant SQLite files live. Ignored on MySQL, where a tenant's
#: database is named `<platform database>_<schema name>` instead.
TENANT_DB_DIR = Path(env("TENANT_DB_DIR", default=str(BASE_DIR / ".tenant-databases")))

if "sqlite" in DATABASES["default"]["ENGINE"]:
    # No server, so no pool to keep warm; a held-open connection only widens
    # the window in which one writer blocks every reader.
    DATABASES["default"]["CONN_MAX_AGE"] = 0
    DATABASES["default"].setdefault("OPTIONS", {}).update(
        {
            "timeout": 20,
            "init_command": (
                "PRAGMA journal_mode=WAL;PRAGMA foreign_keys=ON;PRAGMA synchronous=NORMAL;"
            ),
            # Take the write lock when the transaction opens rather than on
            # the first write. Two things depend on this: concurrent writers
            # queue instead of deadlocking, and `select_for_update()` — which
            # SQLite silently ignores — still gets its mutual exclusion,
            # because the whole transaction holds the database's only lock.
            "transaction_mode": "IMMEDIATE",
        }
    )
elif "mysql" in DATABASES["default"]["ENGINE"]:
    DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)
    DATABASES["default"].setdefault("OPTIONS", {}).update(
        {
            "charset": "utf8mb4",
            # MySQL's default is permissive: it truncates over-long values and
            # accepts '0000-00-00'. Development runs on SQLite, which has no
            # length enforcement at all, so strict mode is the only thing
            # stopping production from silently mangling what dev accepted.
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE'",
        }
    )
else:
    DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Cache / Celery
# --------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "schapp",
    }
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/2")
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "Africa/Lagos"
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_BEAT_SCHEDULE = {
    # Webhooks get lost. This asks the gateway directly about anything still
    # pending, so a parent who has paid is not chasing the bursar tomorrow.
    "finance-sweep-pending-payments": {
        "task": "finance.sweep_pending_payments",
        "schedule": crontab(minute="*/30"),
    },
    # NDPR data minimisation. Spent codes have no reason to persist, and a
    # task nobody schedules deletes nothing — the retention promise is this
    # line, not the function.
    "auth-purge-expired-otps": {
        "task": "apps.auth_phone.tasks.purge_expired_otps",
        "schedule": crontab(hour="3", minute="20"),
    },
    # Spent idempotency keys. Same reasoning as the OTP purge: a table nobody
    # purges grows for the life of the school.
    "api-purge-expired-idempotency-keys": {
        "task": "api.purge_expired_idempotency_keys",
        "schedule": crontab(hour="3", minute="40"),
    },
    # `trial_ends_at` was computed at provisioning and read by nothing, so
    # every trial was permanent. Daily is granularity enough for a date.
    "tenants-expire-trials": {
        "task": "tenants.expire_trials",
        "schedule": crontab(hour="4", minute="0"),
    },
}

#: How long a school keeps working after its trial ends. `PAST_DUE` is
#: servable on purpose — this is the window in which somebody pays, and
#: `tenants.expire_trials` suspends the school when it closes.
TENANT_PAST_DUE_GRACE_DAYS = env.int("TENANT_PAST_DUE_GRACE_DAYS", default=14)

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# A school's own users answer for their own school first; the platform backend
# only ever answers for an active superuser, whose row exists nowhere else.
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "apps.accounts.backends.PlatformSuperuserBackend",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Fernet key protecting per-tenant secrets (gateway keys, SMS creds, NIN).
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")

# --------------------------------------------------------------------------
# Phone auth / SMS / push
# --------------------------------------------------------------------------
SMS_BACKEND = env("SMS_BACKEND", default="apps.communication.sms.console.ConsoleBackend")
PUSH_BACKEND = env("PUSH_BACKEND", default="apps.communication.push.console.ConsolePushBackend")
#: Google service-account JSON for FCM HTTP v1. Only the FCM backend reads it.
FCM_SERVICE_ACCOUNT_FILE = env("FCM_SERVICE_ACCOUNT_FILE", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@schapp.ng")
#: Alphanumeric sender IDs must be pre-registered with the Nigerian operators;
#: an unregistered ID is dropped silently. Tenants may override per school.
SMS_DEFAULT_SENDER_ID = env("SMS_DEFAULT_SENDER_ID", default="SCHAPP")

#: Master OTP accepted for any request_id. Only honoured when DEBUG is on;
#: empty everywhere else. Never set this outside local development.
OTP_DEV_CODE = env("OTP_DEV_CODE", default="")

#: Overrides for apps.auth_phone.ratelimit.DEFAULT_LIMITS, as {name: (hits, seconds)}.
OTP_RATE_LIMITS: dict[str, tuple[int, int]] = {}

# --------------------------------------------------------------------------
# i18n / l10n — Nigeria
# --------------------------------------------------------------------------
LANGUAGE_CODE = "en-ng"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True
CURRENCY_CODE = "NGN"
CURRENCY_SYMBOL = "₦"
PHONENUMBER_DEFAULT_REGION = "NG"

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --------------------------------------------------------------------------
# DRF
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # simplejwt's own class, plus the two things a database-per-tenant
        # deployment needs from it: platform superusers resolved against the
        # platform database, and tenant-less tokens refused inside a school.
        "apps.auth_phone.authentication.PlatformAwareJWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "apps.api.pagination.DefaultCursorPagination",
    "PAGE_SIZE": 50,
    # SearchFilter is global because six viewsets already declare
    # `search_fields` and, without it, every one of those declarations was
    # inert — `?search=` was silently ignored and the office scrolled.
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.api.exceptions.envelope_exception_handler",
    # Same address the audit trail records; see TRUSTED_PROXY_DEPTH. Left unset,
    # DRF keys a throttle on the whole client-supplied X-Forwarded-For string.
    "NUM_PROXIES": TRUSTED_PROXY_DEPTH,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "tenant_signup": "5/hour",
        "otp_request": "10/hour",
        "otp_verify": "20/hour",
        # Per IP, and generous on purpose: an admissions office works through a
        # stack of transcripts in one sitting.
        "document_verify": "240/hour",
    },
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "SCHAPP API",
    "DESCRIPTION": "Multi-tenant school management platform",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # Half the app has a `status` field. Without these the generated Flutter
    # client gets enums called StatusFb3Enum, which is unusable.
    "ENUM_NAME_OVERRIDES": {
        "TenantStatusEnum": "apps.tenants.models.TenantStatus.choices",
        "StudentStatusEnum": "apps.people.models.StudentStatus.choices",
        "StaffStatusEnum": "apps.people.models.StaffStatus.choices",
        "ApplicationStatusEnum": "apps.people.models.ApplicationStatus.choices",
        "EnrolmentStatusEnum": "apps.academics.models.EnrolmentStatus.choices",
        "RegistrationStatusEnum": "apps.academics.models.RegistrationStatus.choices",
        "AttendanceStatusEnum": "apps.attendance.models.AttendanceStatus.choices",
        "PromotionDecisionEnum": "apps.assessment.models.PromotionDecision.choices",
        "InvoiceStatusEnum": "apps.finance.models.InvoiceStatus.choices",
        "PaymentStatusEnum": "apps.finance.models.PaymentStatus.choices",
        "PaymentMethodEnum": "apps.finance.models.PaymentMethod.choices",
        "MarkingMethodEnum": "apps.attendance.models.MarkingMethod.choices",
        "FeeCategoryEnum": "apps.finance.models.FeeCategory.choices",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
}

CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "origin",
    "user-agent",
    "x-requested-with",
    "x-tenant-slug",
    "idempotency-key",
    "if-none-match",
]

# --------------------------------------------------------------------------
# Logging — PII (phone numbers, NINs, OTPs) is redacted before it is emitted.
# --------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact_pii": {"()": "apps.common.logging.RedactPIIFilter"},
    },
    "formatters": {
        "json": {"()": "apps.common.logging.JSONFormatter"},
        "plain": {"format": "%(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["redact_pii"],
            "formatter": "json",
        },
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
    },
}
