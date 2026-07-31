# Architecture

## Tenancy model

One **database** per school. Shared platform data lives in the `default`
connection, which the code calls the `public` tenant.

```
 SQLite (development)                  MySQL (production)
 ┌────────────────────────────────────────────────────────────────┐
 │ schapp.sqlite3            │ schapp              (SHARED_APPS)  │
 │   tenants_tenant, tenants_domain, tenants_plan,                │
 │   tenants_tenantconfiguration, django_celery_beat_*,           │
 │   platform staff, billing, global audit, NCC allocations       │
 ├────────────────────────────────────────────────────────────────┤
 │ .tenant-databases/        │ schapp_kings_college               │
 │   kings_college.sqlite3   │                     (TENANT_APPS)  │
 │   accounts_user, students, results, invoices, attendance …     │
 ├────────────────────────────────────────────────────────────────┤
 │   unity_poly.sqlite3      │ schapp_unity_poly   (TENANT_APPS)  │
 │   accounts_user, students, results, invoices, attendance …     │
 └────────────────────────────────────────────────────────────────┘
```

Isolation is enforced by which *database* a query is sent to, not by a
`tenant_id` column and not by application filters. A forgotten
`.filter(tenant=...)` therefore cannot leak data — the rows are not in the
database at all.

`apps/tenants/db.py` holds the whole mechanism: a thread-local active tenant,
`TenantRouter` (which reads it), `schema_context()` (which sets it) and the
provisioning that creates and migrates a tenant database. Connections for
tenant databases are registered with Django at runtime, because a tenant that
signs up this afternoon cannot be in `settings.DATABASES`.

Two rules keep it honest:

* querying a model from a tenant-only app with **no tenant selected** raises
  `TenantContextRequired` rather than silently reading the platform database;
* `@transaction.atomic` binds to `default` when the module is imported, which
  is the wrong database inside a tenant. Tenant-scoped code uses
  `@tenant_atomic()`, which binds when it is called.

### Why not schemas

`django-tenants` and Postgres `search_path` were the previous design. Neither
SQLite (no schemas at all) nor MySQL (where "schema" *means* "database")
implements that model, so it could not survive the move to those engines. A
database per tenant is what both engines do natively.

## Request path

```
 request
   │
   ├─ SecurityMiddleware
   ├─ CorsMiddleware
   ├─ TenantResolutionMiddleware ──► resolve tenant, select its database
   │      1. X-Tenant-Slug header        (Flutter app)
   │      2. hostname → Domain row       (web, custom domains)
   │      3. JWT tenant_slug claim       (cross-check only, never selects)
   │      fails closed: TENANT_NOT_FOUND / TENANT_SUSPENDED / TENANT_MISMATCH
   ├─ SessionMiddleware / AuthenticationMiddleware   ← tenant already set
   ├─ IdempotencyMiddleware ──► Idempotency-Key on POST/PUT/PATCH/DELETE
   └─ view → service → selector → model
```

The middleware resets the selection to `public` in a `finally` block. The
selection is thread-local and worker threads are reused, so a tenant left set
would be served to whatever request lands on that thread next — a cross-tenant
leak.

Why the JWT claim never *selects* the tenant: a token stolen from school A
would otherwise be replayable against school B by simply changing the header.
The claim only has to agree with the tenant already resolved from the header
or the host.

## Idempotent writes

The Flutter client queues writes it could not send and replays them when the
network returns, which is at-least-once by construction: a request that times
out *after* the server committed is indistinguishable, from the device, from
one that never arrived. Replay it and the school gets a second payment row.

So a queued write carries its outbox id as `Idempotency-Key`, minted **before**
the first attempt and reused on every replay. `IdempotencyMiddleware` claims the
key in `api_idempotencyrecord` — a tenant table, so one school's keys are never
readable from another's — runs the view, and stores the answer:

| second request           | answer                                        |
|--------------------------|-----------------------------------------------|
| same key, same body      | the stored response, `Idempotency-Replayed: true` |
| same key, different body | 422 `IDEMPOTENCY_KEY_REUSED`                  |
| same key, still running  | 409 `IDEMPOTENCY_IN_PROGRESS` + `Retry-After` |

A view that answers non-2xx or raises releases the key, so a failed write is
retryable rather than permanently burned. The middleware runs innermost, after
CSRF and authentication, so a rejected request never consumes a key. Multipart
is skipped: an import replayed is a second intake, and the client never queues
one. `api.purge_expired_idempotency_keys` drops records after seven days —
longer than an outbox survives without signal.

The header is opt-in; nothing changes for a caller that omits it.

## Tenant lifecycle

```
signup ─► PENDING ─► PROVISIONING ─► TRIAL ─► ACTIVE ─► PAST_DUE ─► SUSPENDED ─► ARCHIVED
                          │                                │
                          └─► FAILED (retryable)           └─► ACTIVE (on payment)
```

`PENDING → PROVISIONING` is a compare-and-set `UPDATE`, so two workers cannot
provision the same tenant at once. Database creation never happens inside a
web request: DDL in a request path is how half-built tenants happen.

`TRIAL`, `ACTIVE` and `PAST_DUE` are servable. Suspending on the first late
invoice would lock a school out of its results on the day they are due, so
`PAST_DUE` keeps working and only `SUSPENDED` blocks.

Suspension and reactivation both write to the **platform** audit trail
(`platform.tenant.suspended` / `platform.tenant.reactivated`) with the status
transition and the reason. The school's own database gets no row: it is an act
done *to* the school, and a suspended school's admin should not hold the record
of why.

A `Domain` only selects its tenant once `is_servable` — a custom domain is
typed into the admin before anyone confirms the school controls it, and serving
it in that window hands a session scoped to the school to whoever answers for
that hostname today. The `<slug>.<BASE_DOMAIN>` we issue ourselves needs no
proof.

## The platform admin, inside a tenant

Django's admin reads whatever database the router hands it, so on the platform
host it shows the platform's own tables and nothing of any school —
`TenantOnlyAdminMixin` hides the rest rather than letting it raise
`TenantContextRequired`. Safe, and useless for the job the admin is actually
good at.

A superuser can therefore *select* a school, from the header selector or the
"Work inside this institution" action on the tenant list. The slug goes in the
session and `AdminTenantSwitchMiddleware` re-selects that tenant on every later
`/admin/` request; every tenant-only page then appears and reads and writes
that school's database until the superuser leaves. A banner names the school on
every page, because the only thing between an edit to the right institution and
an edit to the wrong one is knowing which is selected.

Three constraints shape it:

* **After authentication, never before.** `apps.accounts` is in both halves of
  the app split, so the user table exists in the platform database *and* in
  every school's. Selecting the tenant before the session's user id resolves
  would look the superuser up in the school's table, where they have no row,
  and log them out on the page they asked for. The middleware reads
  `request.user` first — that access is not a formality, it is what forces the
  lookup onto `default` while `default` is still selected.
* **Superusers only.** Permissions are rows, and rows move with the selected
  database; a staff account's grants would silently change meaning at the
  moment of the switch. A superuser has none to change — `has_perm`
  short-circuits on `is_superuser` before reaching a database.
* **`/admin/` only.** An API request on the platform host does not inherit the
  selection. A tenant chosen by a cookie is exactly what
  `TenantResolutionMiddleware` refuses everywhere else.

Entering and leaving are audited on the platform trail
(`platform.tenant.entered` / `platform.tenant.exited`), named by tenant, for
the same reason suspension is: the school's own database has no row for an act
done to it from outside.

One thing had to be fixed for any of this to work at all. `LogEntry` is a
shared-app model, so admin history is always written to `default` — but
`ContentType.objects.get_for_model` obeys the router, so from inside a tenant
the entry carried a content-type id from *that school's* table. The two tables
list different apps and their ids do not line up, so the foreign key failed and
saving any object from inside a tenant raised. `pin_admin_content_types()`
resolves both of Django's lookups on the platform connection, unconditionally:
the row lives in the platform database, so its content type belongs there too.

## A superuser signing in to a school

The selector above covers the platform host. On a school's own address —
`<slug>.<BASE_DOMAIN>/admin/`, or any API call carrying `X-Tenant-Slug` — the
tenant is resolved *before* authentication, so the login itself lands in that
school's `accounts_user` table, where the platform's staff have no row.

`apps.accounts.platform` states the rule that fixes it: **a platform superuser
is authenticated against the platform database, and everything their session
owns is written there too.** Devices, token families, blacklisted refresh
tokens and OTP challenges all hold a foreign key to the user, and a school's
database cannot store a key pointing into another one. Only the tenant data
they then read and write follows the selected school.

| Surface | What answers |
|---|---|
| `<school>/admin/` | `PlatformSuperuserBackend`, listed after `ModelBackend` so a school's own user always wins on their own host |
| OTP / PIN login | the phone is looked up in the school first; a platform superuser is the fallback, and their challenge is written to `default` |
| Bearer token | `PlatformAwareJWTAuthentication` — a `platform: true` claim resolves the id against `default` |

Consequences worth knowing:

* Nothing is copied into a school, so revocation is immediate and complete:
  demote or deactivate the account and the next request resolves the same row
  and refuses. There is no mirrored god-account to hunt down afterwards.
* `apps.auth_phone` is in both halves of the app split for this, the same way
  `apps.accounts` and `apps.audit` already were.
* Their actions inside a school are written to the **platform** trail, named by
  `tenant_slug` — same rule as entering and leaving above.
* A token that names no tenant is refused inside one. Ids are per database, so
  id 7 is a different person in every school; without that check a platform
  token plus a header would authenticate as whoever holds that id there.

Plan seat limits (`max_students`, `max_staff`) are enforced in
`apps.people.services`, on the create path every route funnels through — the
CSV import and the admissions conversion included. `null` means unlimited, and
graduated, withdrawn, transferred and soft-deleted records free their seat.
The other `Plan` fields are marked PLACEHOLDER in the model; there is no
billing module for them to drive.

## Institution types

`SECONDARY` and `TERTIARY` are one codebase, switched at runtime. Every
user-visible noun goes through `apps.tenants.labels.labels_for()`:
Term↔Semester, Class↔Level, Subject↔Course. Tenants may override individual
labels. No screen or PDF template hardcodes these words.

## Inbound webhooks

Three callers cannot send `X-Tenant-Slug`: a payment gateway, an SMS provider
reporting delivery, and a biometric gate terminal. The first two name their
school in the URL, under `/api/v1/public/…`, which the middleware serves
without a tenant; the view resolves the `Tenant` itself and enters that
school's database once the signature checks out. The gate terminal is already
inside a tenant path and carries a per-device HMAC.

The signature is always verified against the **raw body** before anything is
parsed, and an event we cannot act on is answered `200` — a provider told
"unknown" retries the same unknown thing for a day. See `docs/deployment.md`
for the URLs and the per-provider credential.

## Secrets

Payment-gateway keys, SMS credentials and NINs use `EncryptedTextField`
(Fernet, key from `FIELD_ENCRYPTION_KEY`). They are never returned by list
endpoints and are excluded from the admin.

A value the current key cannot decrypt reads as empty, so nothing crashes deep
in a serializer — but it is a distinct `UNREADABLE` sentinel, not `""`, and
`pre_save` **refuses to write it back**. That refusal is the whole point: the
secrets share a row with ordinary settings, and both the Django admin and
`InstitutionSettingsSerializer` save through `Model.save()`, which writes every
column. With the wrong key deployed, one edit to a school's crest would
otherwise have replaced its gateway keys, SMS credentials and NIN with blanks
that restoring the correct key could not bring back. The failure is now loud —
an `ImproperlyConfigured` naming the field — and logged at ERROR on the read
that discovered it. Phone numbers, NINs and OTPs are
stripped from logs by `apps.common.logging.RedactPIIFilter` before any handler
sees them — NDPR treats them as personal data and logs get shipped offsite.
