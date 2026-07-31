# Phone authentication

## Normalisation

`apps.numbering.msisdn.normalize(raw)` is the only way a phone number enters
the system. Every one of these lands on `+2348031234567`:

| Input | |
|---|---|
| `08031234567` | local |
| `8031234567` | no trunk 0 |
| `2348031234567` | country code, no `+` |
| `+234 803 123 4567` | spaced |
| `+234-803-123-4567` | hyphenated |
| `234 0803 123 4567` | double-prefixed (contact exports do this) |

Two layers, in order:

1. **libphonenumber** — is this shape possible at all?
2. **The NCC allocation table** — is this prefix actually assigned, and is it
   a personal line?

We check `is_possible_number`, not `is_valid_number`. libphonenumber's bundled
prefix metadata trails NCC assignments by months, and a school must not be
locked out because a data file inside a Python package is stale. The NCC
table in our own database is the authority.

### Adding a prefix

Edit `apps/numbering/fixtures/ncc_mobile_allocations.json`, then:

```bash
python manage.py sync_ncc_allocations
```

No code change, no migration, no deploy. `--prune` marks NDCs absent from the
fixture as `WITHDRAWN`.

The shipped fixture was transcribed from the live NCC table on **2026-07-25**
(`version: 2026-07`). Re-check it whenever the NCC publishes an update —
that is a data edit and one command, never a deploy.

Blocks that exist but can never be a login: `0700` and `0800` (VAS/SNS shared
licensee blocks) and `0900` (reserved for vanity numbers) are stored with
`allows_user_accounts = false` and rejected as `MSISDN_NOT_PERSONAL`. `0709`
and `0819` are `WITHDRAWN`. NDCs absent from the table (`0910`, `0914`,
`0917`, `0918`) are rejected as `MSISDN_UNALLOCATED_PREFIX` — absence is the
answer, so a newly assigned block is added rather than un-hidden.

### Operator is a hint, never a rule

`operator_hint()` returns MTN/Glo/Airtel/9mobile from the prefix. Nigeria has
had Mobile Number Portability since 2013, so the prefix does **not** identify
the current network. Use it for routing-cost estimates and analytics only —
never for validation, blocking or billing. The SMS gateway resolves the real
network at send time.

## OTP flow

```
POST /api/v1/auth/otp/request/   {phone, purpose}
  → 200 {request_id, expires_in, resend_after, masked_phone}      ALWAYS

POST /api/v1/auth/otp/verify/    {request_id, code, device}
  → 200 {access, refresh, expires_in, new_device, user}
```

- 6 digits from `secrets`, stored as `HMAC-SHA256(SECRET_KEY, "<request_id>:<code>")`.
- **Generated in the Celery worker, not the web process** — the plaintext
  never passes through the broker and never exists in the API process.
- 5-minute TTL, single use; issuing a new code invalidates the previous one.
- The request endpoint returns an identical body for numbers with no account.
  No SMS is sent, but the `request_id` is real and even a correct-looking code
  fails with the same `OTP_INVALID` as a wrong one. Do not infer account
  existence from any response on this path.

### Rate limits

All Redis-backed, all overridable via `settings.OTP_RATE_LIMITS`.

| Limit | Default |
|---|---|
| per number, burst | 1 / 60s |
| per number, hourly | 5 / hour |
| per number, daily | 10 / day |
| per IP | 20 / hour |
| per tenant | 50 / hour |
| wrong codes | 5, then a 30-minute lockout |

Resend backs off 60s → 120s → 240s … capped at 15 minutes. Every rejection is
`429` with a `Retry-After` header and `details.retry_after`.

### SMS delivery

`SmsBackend` implementations live in `apps/communication/sms/`. OTP always
goes over the **transactional** route (Termii's `dnd` channel): promotional
routes are blocked for numbers on the NCC Do-Not-Disturb list, which is most
of Nigeria. The sender ID must be pre-registered with the operators or the
message is dropped silently.

## Tokens

Access 15 minutes, refresh 30 days with rotation. Claims: `tenant_slug`,
`tenant_schema`, `family`, `device_id`, `roles`.

**Reuse detection.** Each login opens a `TokenFamily`. Rotation blacklists the
old refresh token. If a token that has already been rotated is presented
again, one of the two holders is an attacker and we cannot tell which — so
the entire family is revoked and both must sign in again. The client gets
`TOKEN_REUSE_DETECTED`.

**Tenant binding.** `TenantResolutionMiddleware` compares the token's
`tenant_slug` against the tenant resolved from the header or host and returns
`TENANT_MISMATCH` on disagreement. The claim never *selects* the tenant —
otherwise a stolen token would be replayable at another school by editing one
header.

**Known limit.** Revoking a device or logging out kills the refresh family at
once, but an already-issued access token stays valid for its remaining ≤15
minutes.

## PIN

Staff may set a 6-digit PIN after an OTP login. It only works on a device that
is already registered and unrevoked — a PIN on an unknown device would reduce
a two-factor login to six digits. Trivial PINs (`123456`, repeated digits) are
refused. All PIN failures return one generic `PIN_INVALID`.

## NDPR / NDPA compliance

- Consent is recorded explicitly (`Tenant.consented_at`, `User.consented_at`)
  with a version string.
- `apps.common.logging.RedactPIIFilter` strips phone numbers, NINs and OTP
  codes from every log record before any handler sees it.
- NINs are encrypted at rest, masked (`•••••••8912`) in detail views and
  absent from list endpoints.
- `purge_expired_otps` deletes spent codes after 7 days.
- Audit rows store a masked phone, never the raw MSISDN.

Still outstanding for full compliance: the data-export and deletion endpoints
(Phase 9).

## Error codes

| Code | HTTP | Meaning |
|---|---|---|
| `MSISDN_EMPTY` / `MSISDN_BAD_LENGTH` / `MSISDN_NON_NUMERIC` | 400 | Malformed number |
| `MSISDN_UNALLOCATED_PREFIX` | 400 | Prefix not assigned in the NCC table |
| `MSISDN_NOT_PERSONAL` | 400 | VAS / shared-cost range |
| `OTP_RATE_LIMITED` | 429 | Too many requests; see `Retry-After` |
| `OTP_LOCKED_OUT` | 429 | Too many wrong codes |
| `OTP_INVALID` / `OTP_EXPIRED` / `OTP_ALREADY_USED` | 401 | Verification failed |
| `PIN_INVALID` / `PIN_LOCKED_OUT` / `PIN_WEAK` | 401 / 400 | PIN path |
| `TOKEN_REUSE_DETECTED` / `TOKEN_INVALID` | 401 | Refresh path |
| `TENANT_NOT_FOUND` / `TENANT_SUSPENDED` / `TENANT_MISMATCH` | 404 / 403 | Tenant resolution |
