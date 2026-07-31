# SCHAPP — Multi-tenant school management platform

Django + DRF backend and a Flutter client for Nigerian secondary schools and
small-to-medium tertiary institutions, from one codebase. Database-per-tenant
isolation, phone-number-first authentication with SMS OTP, ₦/NGN,
Africa/Lagos.

SQLite in development, MySQL in production.

Status: **all nine phases complete** — foundation, identity, core domain,
assessment, finance, the Flutter shell and its feature screens, communication
and reporting, hardening. See `docs/phases.md` for what shipped in each and
what was deliberately left out.

## Running the Flutter client

```bash
cd mobile
flutter run          # Android emulator; talks to http://10.0.2.2:8000
flutter run -d chrome --dart-define=SCHAPP_API_BASE=http://localhost:8000
flutter test
```

Point it at a running `manage.py runserver`, choose the school by its slug, and
sign in with a seeded phone number. In development any OTP request accepts the
code `000000` (`OTP_DEV_CODE`), so there is no need to read the console.

## Five-minute local setup

### With Docker (recommended)

```bash
cp .env.example .env
docker compose up -d db redis
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py bootstrap_public_tenant
docker compose run --rm web python manage.py sync_ncc_allocations
docker compose up
```

- API: http://localhost:8000
- Swagger: http://localhost:8000/api/docs/
- Mailhog: http://localhost:8025

### Without Docker

Needs nothing but Python. Development runs on SQLite, and with `REDIS_URL`
unset the dev settings use an in-process cache and run Celery tasks inline.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements/dev.txt
copy .env.example .env          # leave DATABASE_URL unset for SQLite
python manage.py migrate
python manage.py bootstrap_public_tenant
python manage.py sync_ncc_allocations   # loads the NCC numbering plan
python manage.py runserver
```

The platform database is `schapp.sqlite3`; each school gets its own file under
`.tenant-databases/`. Point `DATABASE_URL` at MySQL
(`mysql://schapp:schapp@localhost:3306/schapp`) to develop against the
production engine — the account needs `CREATE DATABASE`, because a school is
provisioned into its own database.

### Create a school

```bash
python manage.py create_tenant kings-college "Kings College" --type SECONDARY --sync
python manage.py create_tenant unity-poly "Unity Polytechnic" --type TERTIARY --sync
```

Or get one with data already in it — a session, classes, staff, pupils with
guardian accounts, marks, computed results, attendance, invoices with part
payments and a published notice:

```bash
python manage.py seed_demo          # creates/tops up "demo-college"
```

It is deterministic and safe to run twice.

Then reach it either way:

```bash
curl http://localhost:8000/api/v1/public/tenants/lookup/?slug=kings-college
curl -H "X-Tenant-Slug: kings-college" http://localhost:8000/healthz
```

For subdomain routing locally, add to your hosts file:
`127.0.0.1  kings-college.localhost`

## Tests

```bash
pytest                          # everything, on SQLite, no server needed
pytest -m "not db_required"     # logic-only subset, no database at all
pytest --cov --cov-report=term-missing
```

## Layout

```
config/          settings split (base/dev/test/staging/prod), celery, urls
apps/
  common/        base models, encrypted fields, PII-redacting logging
  api/           error envelope, pagination, health probes, versioned router
  tenants/       Tenant/Domain/Plan/TenantConfiguration, resolution middleware,
                 provisioning task, labels resolver          [public]
  numbering/     NCC allocation table, MSISDN normalisation   [public]
  accounts/      User, Role, Device, TokenFamily         [public + tenant]
  audit/         append-only audit trail                 [public + tenant]
  auth_phone/    OTP, rate limits, JWT issue/rotate            [tenant]
  people/        students, guardians, staff, admissions        [tenant]
  academics/     sessions, terms, levels, arms, programmes,
                 subjects, enrolment, registration, timetable  [tenant]
  attendance/    student/staff attendance, biometric webhook   [tenant]
  assessment/    components, grading scales, scores, results,
                 GPA/CGPA, promotion, PDF documents             [tenant]
  finance/       fee structures, invoices, payments, Paystack /
                 Flutterwave adapters, receipts, reconciliation [tenant]
  communication/ SMS and push backends (stateless), announcements,
                 message log, delivery reports                  [tenant]
```

`[public]` lives once in the platform database, `[tenant]` once per school, and
`[public + tenant]` exists in both — the public copy holds platform staff and
platform-level audit.

Reporting has no app of its own: `apps/api/reporting.py` owns
`/reports/overview/` and the CSV exports, because it reads five modules and
writes nothing.

Every app follows the same shape: `models` / `serializers` / `services`
(business logic) / `selectors` (reads) / `views` / `permissions` / `tasks` /
`tests`. Views stay thin.

## Docs

- `docs/architecture.md` — tenancy model, request path, isolation guarantees
- `docs/phases.md` — build plan and what is done
- `docs/deployment.md` — VPS / Render deploy, wildcard DNS, TLS
"# SCHAPP" 
