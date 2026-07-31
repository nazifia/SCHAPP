# Deployment

Targets a single Nigerian-latency-friendly VPS (Contabo/Hetzner/DigitalOcean
Lagos-adjacent) or Render/Railway. The only non-obvious requirement is
**wildcard DNS**, because every school gets a subdomain.

## DNS

```
A      myapp.ng          <server-ip>
A      *.myapp.ng        <server-ip>      ← required: kings-college.myapp.ng
CNAME  school-own.ng     myapp.ng         ← per custom domain, verified first
```

## TLS

Wildcard certificates need DNS-01, not HTTP-01:

```bash
certbot certonly --dns-cloudflare \
  --dns-cloudflare-credentials /etc/letsencrypt/cf.ini \
  -d myapp.ng -d '*.myapp.ng'
```

Custom school domains use ordinary HTTP-01 per domain, issued after
`Domain.verified_at` is set.

## Environment

Set every variable in `.env.example`. Mandatory in production:

| Variable | Note |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` |
| `DJANGO_SECRET_KEY` | no default — the app refuses to boot without it |
| `DATABASE_URL` | `mysql://…` — production refuses to boot on anything else. The account needs `CREATE`/`DROP DATABASE`: each school is provisioned into its own `<db>_<school>` database at runtime |
| `REDIS_URL`, `CELERY_BROKER_URL` | |
| `FIELD_ENCRYPTION_KEY` | **rotating this makes existing gateway keys unreadable.** They are not lost — an unreadable secret reads as empty but refuses to be saved over, so the row survives until the right key is back. Saving `TenantConfiguration` while the key is wrong raises `ImproperlyConfigured` naming the field, by design |
| `ALLOWED_HOSTS` | include `.myapp.ng` |
| `CSRF_TRUSTED_ORIGINS` | include `https://*.myapp.ng` |
| `DOCUMENT_VERIFY_BASE_URL` | defaults to `https://<BASE_DOMAIN>`; set it if verification lives elsewhere |

`DJANGO_SECRET_KEY` also signs the verification codes printed on transcripts
and ID cards. **Rotating it invalidates every document already printed** — the
sheets do not change, but scanning them starts answering "invalid signature".
Rotate it only with a reissue plan, and note that `DOCUMENT_VERIFY_BASE_URL`
is baked into paper that outlives the deployment: every card in a wallet
points at whatever it said the day it was printed.

## Per-school secrets

Payment-gateway keys, the SMS sender ID and SMS credentials belong to the
school, not to the deployment, so they live on `TenantConfiguration`
(Fernet-encrypted) and are set in the admin, not in `.env`.

## Provider webhooks

Both webhook paths carry the school in the URL, because a payment processor
and an SMS gateway hold no session and cannot send `X-Tenant-Slug`. Register
these with the provider per school:

```
https://<host>/api/v1/public/finance/webhook/paystack/<school-slug>/
https://<host>/api/v1/public/finance/webhook/flutterwave/<school-slug>/
https://<host>/api/v1/public/communication/delivery/<school-slug>/
```

Credentials, in order of strength:

| Endpoint | Check |
|---|---|
| Paystack | HMAC-SHA512 over the raw body, keyed by the secret key |
| Flutterwave | `verif-hash` header compared, constant-time, to the stored secret |
| Delivery reports | `X-Delivery-Secret` header compared to `sms_credentials` |

Flutterwave's shared hash proves the sender knows the secret and nothing about
the body, which is why the credited amount is always the one the gateway's own
payload reports and is checked against the invoice. A webhook that never
arrives is not a lost payment: `finance.sweep_pending_payments` runs every 30
minutes and re-verifies anything still `PENDING`, and a bursar can force the
same check from `POST /finance/payments/{id}/verify/`.

The sweep also gives up. Most `PENDING` rows are abandoned carts — a parent
opened the checkout and closed the tab — and nothing else in the system ever
resolved one, so without a floor the task re-asked the gateway about every
checkout it had ever written, every half hour, forever. After
`ABANDON_AFTER_DAYS` (2) a row is marked `FAILED` and stops being swept. That
is not final: `confirm_payment` still takes a `FAILED` row to `SUCCESS`, so a
late webhook or a manual verify can still credit a payment that really
happened.

## First boot

```bash
python manage.py migrate                       # platform database
python manage.py bootstrap_public_tenant --domain myapp.ng
python manage.py createsuperuser
python manage.py collectstatic --noinput
```

## Processes

```
web     gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3
worker  celery -A config worker -l info
beat    celery -A config beat -l info -S django_celery_beat.schedulers:DatabaseScheduler
```

Probes: `/healthz` for liveness (never touches the DB), `/readyz` for
readiness (DB + cache).

## Migrations after a release

```bash
python manage.py migrate            # platform database only
python manage.py migrate_tenants    # every provisioned school
```

The release that moved `apps.auth_phone` into `SHARED_APPS` — so a platform
superuser can sign in to a school — adds no migration file, and a plain
`migrate` will not help it either. Django records a migration as applied on a
connection even when the router filtered out every one of its operations, so
`auth_phone.0001_initial` is already listed as applied on the platform database
while its table was never created there. Once per platform database:

```bash
python manage.py migrate auth_phone zero --fake   # unrecord; nothing ever ran here
python manage.py migrate auth_phone               # create the table for real
```

Only the platform database. Tenant databases have the table already, and
`migrate` without `--database` never touches them.

`migrate` does **not** touch tenant databases; a release that adds a column to
a tenant app is not deployed until `migrate_tenants` has run. It is O(number
of schools) — run it in the worker, not in a deploy hook with a timeout. Add
`--keep-going` to collect every failure instead of stopping at the first.

## Backups

Nightly `mysqldump --all-databases` plus a per-tenant
`mysqldump <db>_<school>`, so one school can be restored without touching the
others — a database per tenant is what makes that a single command. A restore
drill belongs in Phase 9 and is not yet documented as tested.
