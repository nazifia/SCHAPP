"""Database-per-tenant routing.

One database per school plus one shared ``default`` database for the platform.
Postgres schemas are gone: SQLite (development) has no schemas at all and MySQL
(production) uses "schema" as a synonym for "database", so *a database per
tenant* is the one model both engines implement natively.

The active tenant is thread-local. :class:`TenantRouter` reads it,
:func:`schema_context` and the middleware set it, and every tenant connection is
registered with ``django.db.connections`` on first use — tenant databases are
created at runtime, so they can never be listed in ``settings.DATABASES``.

Two rules keep this honest:

* a model from a tenant-only app queried with no tenant selected raises
  :class:`TenantContextRequired` rather than quietly reading the platform
  database;
* ``transaction.atomic`` binds to ``default`` at import time, which is the wrong
  database inside a tenant. Tenant-scoped code uses :class:`tenant_atomic`,
  which binds at call time.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
from contextlib import ContextDecorator
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import DEFAULT_DB_ALIAS, connections, transaction

#: Tenant identifiers become database names and file names, so they land in DDL
#: that cannot be parameterised. Nothing outside this shape is ever allowed
#: through, and the shape is deliberately narrower than either engine accepts.
SCHEMA_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,49}$")
DATABASE_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")

_state = threading.local()


class TenantContextRequired(RuntimeError):
    """A tenant-only model was queried with no tenant selected."""


class UnsafeTenantName(ValueError):
    """A tenant identifier that must not be interpolated into DDL."""


# ---------------------------------------------------------------------------
# App-label sets, derived from the SHARED_APPS / TENANT_APPS split in settings
# ---------------------------------------------------------------------------
def _labels(app_paths) -> frozenset[str]:
    return frozenset(path.rsplit(".", 1)[-1] for path in app_paths)


@lru_cache(maxsize=1)
def shared_labels() -> frozenset[str]:
    return _labels(settings.SHARED_APPS)


@lru_cache(maxsize=1)
def tenant_labels() -> frozenset[str]:
    return _labels(settings.TENANT_APPS)


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------
def is_public(schema_name: str | None) -> bool:
    return not schema_name or schema_name == settings.PUBLIC_SCHEMA_NAME


def validate_schema_name(schema_name: str) -> str:
    if not SCHEMA_NAME_RE.match(schema_name or ""):
        raise UnsafeTenantName(f"Refusing to use {schema_name!r} as a tenant identifier.")
    return schema_name


def alias_for(schema_name: str | None) -> str:
    """Connection alias for a tenant. The public tenant *is* ``default``."""
    if is_public(schema_name):
        return DEFAULT_DB_ALIAS
    return f"tenant_{validate_schema_name(schema_name)}"


def is_sqlite(engine: str) -> bool:
    return "sqlite" in engine


def _settings_for(schema_name: str) -> dict:
    """Tenant connection settings: the platform's, pointed at another database.

    Copied from the *live* default connection rather than from
    ``settings.DATABASES`` so that a test run — where Django has already
    rewritten NAME to ``test_…`` — produces ``test_…`` tenant databases too.
    """
    base = dict(connections[DEFAULT_DB_ALIAS].settings_dict)
    base["OPTIONS"] = dict(base.get("OPTIONS") or {})

    if is_sqlite(base["ENGINE"]):
        base["NAME"] = str(Path(settings.TENANT_DB_DIR) / f"{schema_name}.sqlite3")
    else:
        name = f"{base['NAME']}_{schema_name}"
        if not DATABASE_NAME_RE.match(name):
            raise UnsafeTenantName(
                f"{name!r} is not a usable MySQL database name "
                "(letters, digits and underscores only, 64 characters max)."
            )
        base["NAME"] = name

    # Nothing ever calls Django's test-database setup for these aliases, so the
    # test name must already be the real one.
    base["TEST"] = {**(base.get("TEST") or {}), "NAME": base["NAME"], "MIRROR": None}
    return base


def register(schema_name: str | None) -> str:
    """Make a tenant's connection known to Django. Returns its alias."""
    alias = alias_for(schema_name)
    if alias != DEFAULT_DB_ALIAS and alias not in connections.databases:
        connections.databases[alias] = _settings_for(schema_name)
    return alias


# ---------------------------------------------------------------------------
# The active tenant
# ---------------------------------------------------------------------------
def current_schema() -> str:
    return getattr(_state, "schema", None) or settings.PUBLIC_SCHEMA_NAME


def current_alias() -> str:
    return getattr(_state, "alias", DEFAULT_DB_ALIAS)


def set_schema(schema_name: str | None) -> str:
    alias = register(schema_name)
    _state.schema = settings.PUBLIC_SCHEMA_NAME if is_public(schema_name) else schema_name
    _state.alias = alias
    return alias


def set_tenant(tenant) -> str:
    return set_schema(getattr(tenant, "schema_name", None))


def set_public() -> str:
    return set_schema(None)


class schema_context(ContextDecorator):
    """Run a block against one tenant's database, then restore the previous one.

    Usable as ``with schema_context(name):`` or as a ``@schema_context(name)``
    decorator; either way the tenant is resolved when the block is entered.
    """

    def __init__(self, schema_name: str | None):
        self.schema_name = schema_name

    def __enter__(self):
        self._previous = current_schema()
        set_schema(self.schema_name)
        return self

    def __exit__(self, *exc_info) -> bool:
        set_schema(self._previous)
        return False

    def _recreate_cm(self) -> schema_context:
        return schema_context(self.schema_name)


def tenant_context(tenant) -> schema_context:
    return schema_context(getattr(tenant, "schema_name", None))


class tenant_atomic(ContextDecorator):
    """``transaction.atomic`` on whichever database is active *at call time*.

    ``@transaction.atomic`` resolves its connection when the module is imported,
    which is always ``default`` — so in a tenant it would open a transaction on
    the platform database while the writes went somewhere else. This resolves on
    entry instead. Use it as ``with tenant_atomic():`` or ``@tenant_atomic()``.
    """

    def __enter__(self):
        self._atomic = transaction.atomic(using=current_alias())
        return self._atomic.__enter__()

    def __exit__(self, *exc_info):
        return self._atomic.__exit__(*exc_info)

    def _recreate_cm(self) -> tenant_atomic:
        return tenant_atomic()


def on_commit(func) -> None:
    """``transaction.on_commit`` against the active tenant's connection."""
    transaction.on_commit(func, using=current_alias())


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
class TenantRouter:
    def _db(self, model, **hints):
        label = model._meta.app_label
        if label not in tenant_labels():
            return DEFAULT_DB_ALIAS

        instance = hints.get("instance")
        if (
            instance is not None
            and instance._state.db == DEFAULT_DB_ALIAS
            and label in shared_labels()
        ):
            # Related rows belong to the database that holds the row they hang
            # off — a key means nothing anywhere else. This is what lets a
            # platform superuser keep their own roles, devices and sessions
            # while a school is selected: their user row was read from
            # `default`, so `user.devices` is read from there too.
            return DEFAULT_DB_ALIAS

        alias = current_alias()
        if alias != DEFAULT_DB_ALIAS:
            return alias
        if label in shared_labels():
            # Listed in both: the platform keeps its own copy in `default`.
            return DEFAULT_DB_ALIAS
        raise TenantContextRequired(
            f"{model._meta.label} only exists inside a tenant database. "
            "Wrap the call in apps.tenants.db.schema_context(...)."
        )

    db_for_read = _db
    db_for_write = _db

    def allow_relation(self, obj1, obj2, **hints) -> bool:
        # There are no cross-database relations in this project: every foreign
        # key points at a model in the same half of the split.
        return True

    def allow_migrate(self, db, app_label, **hints) -> bool:
        if db == DEFAULT_DB_ALIAS:
            return app_label in shared_labels()
        return app_label in tenant_labels()


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------
def create_database(schema_name: str, *, verbosity: int = 0) -> str:
    """Create the tenant's database if absent and migrate it. Idempotent."""
    alias = register(schema_name)
    settings_dict = connections[alias].settings_dict

    if is_sqlite(settings_dict["ENGINE"]):
        target = Path(settings_dict["NAME"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(_sqlite_template(verbosity=verbosity), target)
            return alias
    else:
        with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings_dict['NAME']}` CHARACTER SET utf8mb4"
            )

    call_command(
        "migrate", database=alias, interactive=False, verbosity=verbosity, run_syncdb=False
    )
    return alias


_template_lock = threading.Lock()


def _migration_fingerprint() -> str:
    """Identity of the tenant schema, from the migrations on disk."""
    from django.db.migrations.loader import MigrationLoader

    names = sorted(f"{app}.{name}" for app, name in MigrationLoader(None).disk_migrations)
    return hashlib.sha256("\n".join(names).encode()).hexdigest()[:12]


def _sqlite_template(*, verbosity: int = 0) -> Path:
    """Path to a migrated, empty tenant database, built once and then copied.

    Migrating a tenant takes seconds; copying a file takes milliseconds, and
    every tenant database is byte-identical at the moment it is created. Only
    SQLite gets this — MySQL has no comparably cheap copy.

    ponytail: the template is keyed on which migrations exist, not on what they
    contain, so *editing* a migration in place leaves a stale template behind.
    Delete the file (or the whole TENANT_DB_DIR) when you do that.
    """
    fingerprint = _migration_fingerprint()
    template = Path(settings.TENANT_DB_DIR) / f"_template-{fingerprint}.sqlite3"

    with _template_lock:
        if template.exists():
            return template
        template.parent.mkdir(parents=True, exist_ok=True)
        # `tenant_` prefix: it is a tenant-shaped connection, and the test suite
        # allows tenant aliases by prefix.
        alias = f"tenant_template_{fingerprint}"
        building = template.with_suffix(f".{os.getpid()}.building")
        connections.databases[alias] = {
            **_settings_for("unused"),
            "NAME": str(building),
            "TEST": {"NAME": str(building), "MIRROR": None},
        }
        try:
            call_command(
                "migrate", database=alias, interactive=False, verbosity=verbosity, run_syncdb=False
            )
            connections[alias].close()
            # Rename last: a crash mid-migrate must not leave a half-built file
            # that the next call would happily copy.
            building.replace(template)
        finally:
            for leftover in (building, Path(f"{building}-wal"), Path(f"{building}-shm")):
                leftover.unlink(missing_ok=True)
            connections.databases.pop(alias, None)
    return template


def database_exists(schema_name: str) -> bool:
    alias = register(schema_name)
    settings_dict = connections[alias].settings_dict
    if is_sqlite(settings_dict["ENGINE"]):
        return Path(settings_dict["NAME"]).exists()
    with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            [settings_dict["NAME"]],
        )
        return cursor.fetchone() is not None


def drop_database(schema_name: str) -> None:
    """Destroy a tenant's database. Only provisioning teardown and tests call this."""
    alias = alias_for(schema_name)
    if alias == DEFAULT_DB_ALIAS or alias not in connections.databases:
        return

    settings_dict = connections[alias].settings_dict
    name, engine = settings_dict["NAME"], settings_dict["ENGINE"]
    connections[alias].close()

    if is_sqlite(engine):
        target = Path(name)
        target.unlink(missing_ok=True)
        # WAL leaves siblings behind; an orphaned -wal is a future corruption.
        for suffix in ("-wal", "-shm"):
            Path(f"{name}{suffix}").unlink(missing_ok=True)
    else:
        with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")

    connections.databases.pop(alias, None)
