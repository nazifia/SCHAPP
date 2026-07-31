# Build phases

| # | Phase | Status |
|---|-------|--------|
| 1 | Foundation — scaffold, Compose, settings split, CI, base models, tenant+domain, provisioning, health | **done** |
| 2 | Identity — MSISDN + NCC allocation table, OTP, JWT, roles, audit, devices | **done** |
| 3 | Core domain — people, academic structure, enrolment, timetable, attendance | **done** |
| 4 | Assessment — scores, grading engine, report cards, GPA/CGPA, transcripts, PDF | **done** |
| 5 | Finance — fees, invoices, Paystack/Flutterwave, receipts, reconciliation | **done** |
| 6 | Flutter shell — design system, responsive scaffolding, routing, auth, offline layer | **done** |
| 7 | Flutter features — role dashboards, score/attendance entry, admin console | **done** |
| 8 | Communication & reporting — SMS/email/push, announcements, analytics, exports | **done** |
| 9 | Hardening — load test, security review, a11y, docs, demo tenant, release build | **done** |

## Phase 1 — what shipped

- `config/` settings split: base / dev / test / staging / prod
- Docker Compose (web, db, redis, worker, beat, mailhog) + multi-stage Dockerfile
- GitHub Actions: ruff → black → mypy → pytest with coverage gate at 80%
- `apps/common`: `TimeStampedModel` (UUID pk), `SoftDeleteModel`,
  `EncryptedTextField`, JSON logging with PII redaction
- `apps/api`: error envelope + machine codes, cursor pagination, `/healthz`, `/readyz`
- `apps/tenants`: `Tenant`, `Domain`, `Plan`, `TenantConfiguration`,
  `TenantResolutionMiddleware`, idempotent `provision_tenant` task,
  labels resolver, self-serve signup + lookup API, admin, management commands

## Phase 1 — deliberate deferrals

| Deferred | Lands in |
|---|---|
| `seed_tenant()` now seeds roles; grading scales and terms still pending | 3–4 |
| `TenantTask` Celery base class — `schema_context` inline is enough so far | 3 |
| Billing/subscription records beyond `Plan` | 5 |
| Custom-domain verification flow (`Domain.verified_at` is unused) | 9 |
| ETag / `updated_since` / `?expand=` on list endpoints — no list endpoints yet | 3 |

## Phase 2 — what shipped

- `apps/numbering` (platform database): `MobileNumberAllocation`, `msisdn.py`
  (`normalize` / `to_nsn` / `operator_hint` / `format_display` / `mask`),
  `PhoneNumberField`, versioned NCC fixture, `sync_ncc_allocations`
- `apps/accounts` (public **and** tenant): phone-first `User`, `Role` +
  code-declared permission catalogue, `Device`, `TokenFamily`,
  `RequirePermission` DRF class
- `apps/auth_phone` (tenant): `OtpRequest`, Redis rate limits, OTP
  issue/verify, JWT issue/rotate with reuse detection, PIN login, device
  list/revoke, `/me`
- `apps/audit` (public **and** tenant): append-only `AuditLog`, `record()`
- `apps/communication`: `SmsBackend` interface, Console/LocMem/Termii adapters
- Roles seeded into every new tenant database during provisioning

## Phase 2 — deliberate deferrals

| Deferred | Why / lands in |
|---|---|
| BulkSMSNigeria, Africa's Talking, Twilio adapters | Interface is done; adding one is ~40 lines. Phase 8, once a provider is contracted |
| Voice-OTP and WhatsApp fallback | Needs a second contracted provider. Phase 8 |
| Delivery-report webhooks | Fields (`delivery_status`, `delivery_message_id`) exist; the endpoint is Phase 8 |
| Access-token revocation is not instant | Revoking a device or logging out kills the refresh family immediately; an already-issued access token stays valid for its remaining ≤15 minutes. Per-request family lookup would cost a query on every call — revisit in Phase 9 with a cached revocation list |
| Guardian → multiple wards | Needs the student model. Phase 3 |
| `globally_unique_phone` config flag is not enforced | Per-tenant uniqueness is the DB constraint and is the correct default (parents legitimately have accounts at two schools). Platform-wide uniqueness needs a platform-wide phone registry — only if a customer actually asks |
| Platform-staff impersonation | Audit action `platform.impersonation` is declared; the flow itself is Phase 9 |
| NIN verification against NIMC | Format-validated, encrypted and masked; no NIMC integration |

## Phase 3 — what shipped

- `apps/academics`: `AcademicSession`, `Term`, `ClassLevel`, `Stream`,
  `Faculty`, `Department`, `Programme`, `ClassArm`, `Subject`,
  `TeachingAssignment`, `Enrolment`, `SubjectRegistration`, `Room`,
  `TimetableEntry` — one table set for both institution types
- `apps/people`: `Student`, `Guardian`, `StudentGuardian`, `Staff`,
  `Application`, `StudentDocument`, configurable admission/matric/staff
  number generators, admissions state machine, object-level scoping selectors
- `apps/attendance`: `StudentAttendance`, `StaffAttendance`,
  `BiometricDevice`, `BiometricEvent`, transactional bulk marking with
  version-checked conflict resolution, HMAC-signed device webhook
- `apps/api`: `UpdatedSinceMixin`, `ETagMixin`, `ExpandableSerializerMixin`,
  `atomic_bulk` — the sync and bulk primitives promised in Phase 1
- Provisioning now seeds levels (JSS1–SSS3 or 100–500 Level) and streams
- 73 API endpoints, OpenAPI clean

## Phase 3 — deliberate deferrals

| Deferred | Why / lands in |
|---|---|
| Prerequisites check "registered before", not "passed" | Scores do not exist yet. One lookup in `unmet_prerequisites` changes in Phase 4; no call site moves |
| Promotion engine | Needs end-of-year results. `ClassLevel.next_level` and `Enrolment.promotion_note` are in place for it. Phase 4 |
| ID cards with QR | ~~Phase 8~~ — **done**, see "Closed after Phase 9" |
| Deletion sync for offline clients | `updated_since` returns changes, not tombstones; clients reconcile deletions with a periodic full pull. Revisit if it bites |
| Virus scan on uploads | `StudentDocument.virus_scanned_at` exists; the scanner hook is Phase 9 |
| Timetable auto-generation | Clash *detection* is done; automatic scheduling was never in scope |
| Bulk CSV import of students | ~~Phase 8~~ — **done**, see "Closed after Phase 9" |

## Phase 4 — what shipped

- `apps/assessment`: `AssessmentComponent` (CA1/CA2/Exam as data, scoped to the
  school, a level or a single subject), `GradingScale` + `GradeBand`, `Score`,
  and the two derived tables `SubjectResult` and `TermResult`
- `grading.py` — the arithmetic as pure functions over `Decimal`: percentage,
  band resolution, competition ranking, credit-weighted GPA. No database, so
  the rules a school argues about are tested without a database
- Bulk score entry with the same version-checked conflict handling as
  attendance, plus a `sheet` endpoint that hands a teacher the whole mark sheet
  for offline entry
- `recompute_term` — a rebuild, not an increment: subject totals, grades,
  subject positions and class statistics, then term averages, GPA, CGPA,
  attendance and term positions. Running it twice gives the same answer
- Publication gate: a term cannot be published while any counted registration
  is missing a mark, and publication is what makes a result visible to a parent
- Promotion engine: `decide_promotions` (dry run) → override → `apply_promotions`,
  which enrols the cohort into the next session and graduates the terminal year
- PDF report card, broadsheet and transcript from Django templates via
  `xhtml2pdf` — no browser and no GTK/Pango in the container
- Provisioning now seeds the WAEC nine-point or 5-point CGPA scale and the
  matching mark-sheet columns
- 21 new endpoints (94 in total), OpenAPI clean

Two Phase 3 deferrals closed: prerequisites now check a **passing** result
rather than a prior registration, and the promotion engine exists.

## Phase 4 — deliberate deferrals

| Deferred | Why / lands in |
|---|---|
| `recompute_term` runs inline, not on Celery | It is `update_or_create` per row and a term is a few thousand rows. It is already a service, so moving it behind a task is a decorator when a school gets big enough to notice |
| Result-checker PINs / scratch cards | The publication gate and per-user scoping already control who sees a result. Card sales are a Phase 5 billing feature, not an assessment one |
| Behavioural / affective domain ratings (punctuality, neatness) | Pure report-card furniture — a `JSONField` on `TermResult` when a customer asks for it |
| Automatic form-teacher comments from the average | Schools want their own wording; the two comment fields are free text and the engine writes nothing |
| Cumulative average for secondary schools | CGPA is null where there are no credit units and the report card prints the term average. A cumulative *average* across terms is one aggregate when a school asks |
| Transcript authenticity QR / signature | ~~Phase 8~~ — **done**, see "Closed after Phase 9" |
| Grade bands are read as lower bounds only | A percentage of 73.33 falls in the gap between whole-number bands, so the upper bound is validation, not grading. Documented in `grading.band_for` |

## Phase 5 — what shipped

Built after Phase 6, not before it: the shell needed nothing from finance, and
the fee screens wanted a shell to sit in. Nothing else was reordered.

- `apps/finance`: `FeeStructure` + `FeeItem` (the price list, scoped by
  session, term and optionally level or programme), `Invoice` + `InvoiceLine`,
  `Payment`. Invoice lines are **snapshotted** at generation, so a January
  price rise does not rewrite a September bill
- `Invoice.amount_paid` is derived, never typed: `recompute_paid()` is the only
  writer and it rebuilds from the payments, so a reversal two weeks later lands
  on the right number without anyone remembering the old one
- `gateways/` — the same pluggable shape as the SMS backends: a `Paystack`
  adapter (kobo arithmetic, HMAC-SHA512 body signature), a `Flutterwave` one
  (naira, `verif-hash`), and a `manual` one so the whole online flow is
  testable without an account. Keys come from the tenant's encrypted
  configuration, so two schools collect into their own bank accounts
- `confirm_payment` is the single path to a successful online payment, whether
  the news came from the webhook, from a bursar clicking verify, or from the
  half-hourly sweep. Idempotent three ways: an already-successful payment is
  untouched, `gateway_reference` is unique, and the amount credited is the one
  the gateway says arrived — not the one we asked for
- Webhooks live under `/api/v1/public/finance/webhook/<gateway>/<slug>/`
  because a processor cannot send `X-Tenant-Slug`. Signature checked against
  the **raw body** before anything is parsed; an unknown reference is answered
  200, since a gateway told "unknown" simply retries it for a day
- Invoice and receipt PDFs, generation for a whole cohort in one transaction,
  waive/cancel with a reason, collection summary and an overdue list
- 25 new endpoints (paths, counting the two public webhooks), OpenAPI clean;
  30 tests covering the money paths — over-payment refused, a forged webhook
  changes nothing, a replayed one credits the invoice once, a reversal restores
  the balance, and the unique constraint that guarantees it is exercised
  directly

## Phase 5 — deliberate deferrals

| Deferred | Why / lands in |
|---|---|
| Payroll | `finance.manage_payroll` is in the catalogue and the bank details are already on `Staff`. Nobody has asked; the fee side is what sells |
| Part-payment plans / instalment schedules | A part payment already works — the invoice goes `PART_PAID` and the balance is what is owed. A *schedule* of due dates is a table when a school asks for one |
| Refunds | A reversal exists and is audited. Moving money back out is a bank operation, not an API one, until a school runs it enough to automate |
| Discounts per student (sibling, staff child) | `Invoice.discount` takes the amount and a waiver takes the whole bill. A rules engine for who qualifies is a spreadsheet the bursar already has |
| Result-checker PINs / scratch cards | Carried over from Phase 4. Publication and per-user scoping already control who sees a result; card *sales* need a demand nobody has shown |
| Per-tenant currency | These are Nigerian institutions. `settings.CURRENCY_CODE` is NGN and one field would not be enough anyway (rounding, gateway support) |

## Phase 6 — what shipped

- `mobile/` — Flutter 3.44 app (Android, iOS, web) targeting the DRF API.
  Four packages beyond Flutter itself: `go_router`, `http`,
  `flutter_secure_storage`, `shared_preferences`
- `src/phone.dart` — client-side mirror of `msisdn.py`'s *shape* layer
  (separators, country code, trunk 0, double-prefix, length). The NCC
  allocation check stays server-side; its error messages display as-is
- `src/api/` — one `ApiClient`: `X-Tenant-Slug` header, bearer token, the
  error envelope as `ApiError` (branching on `code` only), single-flight
  refresh-and-retry on 401, 20s timeout, every transport failure read as
  offline
- Offline layer: read-through GET cache keyed by tenant+URL and a write
  outbox, both in `shared_preferences`; replay is oldest-first, 4xx drops the
  entry, 5xx/offline leaves it queued. Flushed on sign-in, app resume and
  pull-to-refresh. Sign-out and school-switch wipe the store
- `Session` (`ChangeNotifier`) — chosen tenant, tokens in secure storage,
  OTP request/verify, PIN login, device list/revoke, tenant `labels` resolver
  so no widget hardcodes Term/Semester
- Routing: go_router with a two-gate redirect (school chosen → signed in),
  driven by `refreshListenable` so sign-out navigates itself
- Design system: Material 3 seeded from the tenant's `primary_color`, light
  and dark, 52dp touch targets; `AppShell` gives bottom bar / rail / extended
  rail at 600/1024dp and one shared offline banner
- Screens: school lookup, sign-in (OTP or PIN), OTP entry with server-driven
  resend backoff, dashboard with pending-sync list, account (PIN, devices,
  sign out)
- CI: `mobile` job — `dart format --set-exit-if-changed`, `flutter analyze`,
  `flutter test` (25 tests: phone table from docs/auth.md, envelope, cache,
  tenant isolation, refresh single-flight, outbox replay)

## Phase 6 — deliberate deferrals

| Deferred | Why / lands in |
|---|---|
| Real feature screens | Phase 7. The dashboard says so instead of showing fake tiles |
| sqflite cache | `shared_preferences` blobs hold ~200 responses; swap when a tenant's cache reaches megabytes |
| Connectivity plugin | App-resume + pull-to-refresh + next-request-failure already cover reconnection |
| Push tokens | `Device.push_token` field exists server-side; FCM wiring is Phase 8 |
| Localisation | English only; `labels` already carries the institution-type wording |
| Queued writes for money-adjacent actions | Outbox replay is at-least-once; nothing is queued that isn't safe to replay until the API takes an idempotency key |

## Phase 7 — what shipped

- `src/api/repository.dart` — one thin naming layer over `ApiClient` for every
  endpoint the screens use. Not a generated client and not a repository per
  module: state stays in the screens, because `ApiClient` is already the cache
- `src/design/async_view.dart` — load / spinner / the API's own message with a
  retry / pull-to-refresh, written once. Also `naira()`, because six screens
  show money and Nigeria writes it one way
- **Dashboard** is role-aware off the *permission codes* the server sends with
  `/auth/me/`, not off role names — a school that invents a "Head of Exams"
  role gets the right tiles without a client release. Same list drives the
  navigation bar, so a teacher, a parent and a bursar get three apps from one
  build
- **Mark entry**: `/academics/my-classes/` fills the pickers in one request,
  then one column of the mark sheet at a time with a numeric keyboard. Submit
  works offline — the batch goes to the outbox, each row carrying the `version`
  the device last saw, so a late sync reports a conflict instead of overwriting
  an office correction
- **Register**: class + date, everyone present by default (in a class of forty
  the exceptions are what a teacher actually knows), four states, offline
  submit on the same versioned path
- **Results**: term results with average, GPA/CGPA where they mean anything,
  position and attendance. Scoped entirely by the API — a guardian's request
  simply comes back with two children in it
- **Fees**: outstanding total, per-invoice lines, and a checkout button that
  opens the gateway in the external browser (`url_launcher`, the one new
  dependency — bank 3-D Secure redirects do not survive an in-app webview).
  Deliberately **not** queued offline: a replayed charge is a second charge
- **Notices**: read, and for whoever holds `communication.manage_announcement`,
  compose and publish — with the confirmation naming the channels, because
  publishing is what texts nine hundred parents and there is no undo
- **Console**: roll, staff, attendance rate, collection rate and the overdue
  list, in one request
- 30 tests; `flutter analyze` clean; the web build compiles

## Phase 7 — deliberate deferrals

| Deferred | Why / lands in |
|---|---|
| In-app PDF viewing | The report card and receipt endpoints return `inline` PDFs; the app hands the URL to the browser rather than shipping a PDF renderer |
| Bursar-side payment recording in the app | `POST /finance/invoices/{id}/pay/` exists and the admin has it. A counter clerk is at a desk with a browser, not on a phone |
| Editing structure (classes, subjects, fee items) from the app | Django admin does it, and it is a once-a-session job for one person |
| A state-management package | Two screens share state and `ChangeNotifier` already carries it. Adding Riverpod to hold four fields is the thing this codebase keeps not doing |
| Localisation | Still English; `labels` covers the institution-type wording |

## Phase 8 — what shipped

- `apps.communication` moved from the platform database into the tenant
  databases — announcements and a send log are one school's own. The SMS and
  push *backends* are stateless and still callable from anywhere
- `Announcement`: audience by role, optionally narrowed to a level or class
  arm; `MessageTemplate` with `{placeholders}` and a forgiving formatter, so a
  typo in a template leaves `{oops}` in the body instead of raising mid-broadcast
- `Message` — one row per recipient per channel, written **before** the send.
  A provider that accepts a message and then times out must not leave the
  school with a bill and no record. This table is also the answer to "was it
  delivered?" and to reconciling the provider's invoice
- Publishing resolves the audience once and stores the result: an in-app copy
  for everybody (free, always) plus the paid channels for whoever has an
  address on them. Notices go on the **promotional** route; only OTP pays the
  transactional/DND rate
- Push: `PushBackend` interface with console/locmem backends and an FCM HTTP v1
  adapter (service-account JWT signed with `cryptography`, token cached until
  expiry). A dead token is reported distinctly and cleared off the `Device` row
  rather than pushed at forever. `POST /auth/devices/push-token/` handles FCM
  rotating a token behind the app's back
- Delivery-report webhook at `/api/v1/public/communication/delivery/<slug>/`,
  shared-secret authenticated, matching on the provider's message id
- `apps/api/reporting.py` — `/reports/overview/` (roll, staff, attendance rate,
  collection summary in one request, because six dashboard tiles fetched
  separately is six round trips on a dropping connection) and
  `/reports/export/<dataset>/` streaming CSV for students, staff, results,
  attendance, invoices and payments. Each dataset carries the permission its own
  module would require, so an export is never a way round object-level scoping
- CSV, not XLSX: every school office opens it, it streams row by row, and it
  needs no dependency

## Phase 8 — deliberate deferrals

| Deferred | Why / lands in |
|---|---|
| Voice-OTP and WhatsApp fallback | Still needs a second contracted provider. The interface has been ready since Phase 2 |
| BulkSMSNigeria / Africa's Talking / Twilio adapters | Termii works; each additional one is ~40 lines against the same interface, written when a school has that contract |
| Read receipts per announcement | `Message.read_at` is set when the inbox is opened, which is enough for an unread badge. Per-parent "seen at 14:12" is surveillance nobody asked for |
| Scheduled / recurring announcements | `published_at` is set by the publish action. A beat task that publishes on a schedule is ten lines once someone wants it |
| Rich text and attachments in notices | Plain text renders identically in the app, in an SMS and in an email. A rich-text notice that arrives as markup in a text message is worse than no formatting |
| SMS credit metering per plan | `Plan.max_sms_per_month` exists and the `Message` log makes the count a query. Enforcement waits for a school to hit it |
| XLSX / PDF exports | CSV opens in Excel. A formatted workbook is a dependency and a template argument |

## Phase 9 — what shipped

- `python manage.py seed_demo` — a provisioned school with a session and three
  terms, six class arms, five subjects, staff, twenty-four pupils each with a
  guardian account, a full mark sheet, computed results, attendance, issued
  invoices with part payments, and a published notice. Every screen in the app
  has something real behind it. Deterministic (`--seed`) and `get_or_create`
  throughout, so running it again tops the demo up
- The finance and communication webhook paths, their signature rules and the
  per-school secret model are documented in `docs/deployment.md`
- One real production bug found and fixed in the pass: MySQL silently **drops**
  a `UniqueConstraint` that carries a `condition`, so `Payment.gateway_reference`
  — the whole idempotency guarantee against a replayed webhook — was protected
  on SQLite and unprotected on the engine that matters. It is now an
  unconditional constraint with a NULL-where-absent column, migrated in place,
  and there is a test that fails if it is ever weakened. The invoice constraint
  cannot be made unconditional (a soft-deleted bill must not block re-billing);
  that one is documented as a development aid, with the real check in the service
- Typing debt cut roughly in half: the `AppError.status_code` annotation that
  was making every subclass an error, and the channel-dispatch and JWT-signing
  types introduced this phase. What remains is django-stubs/DRF-stubs friction
  (`Token | None` on simplejwt, `Field.label` variance), so `mypy` stays
  advisory in CI rather than blocking on other people's stubs
- Full suite: 335 backend tests and 30 Flutter tests green; `ruff`, `black`,
  `dart format` and `flutter analyze` clean; the OpenAPI schema generates
  without warnings; the web build compiles

## Phase 9 — deliberate deferrals

| Deferred | Why |
|---|---|
| Load test | Wants a production-shaped MySQL and a traffic profile from a real school. The shape to test is known — mark entry and results day — but a number from a laptop would be a fiction |
| Restore drill | Backups are documented and a database per tenant makes a single-school restore one command; nobody has *run* it, and saying it is tested when it is not is worse than saying it is not |
| Access-token revocation inside its 15-minute window | Unchanged from Phase 2: revoking a device kills the refresh family immediately. A cached revocation list is the fix if a customer requires an instant cut-off |
| Platform-staff impersonation | The audit action is declared; the flow needs a consent and disclosure story before it needs code |
| Virus scan on uploads | `StudentDocument.virus_scanned_at` is still the hook. It needs a scanner in the deployment, not code here |
| Custom-domain verification | `Domain.verified_at` is still unused; wildcard subdomains cover every school so far |
| Screen-reader audit on a real device | Material's semantics, 52dp targets and the theme's contrast are in place; a TalkBack pass with an actual user is worth more than another round of guessing |

## Closed after Phase 9

Three items were promised to Phase 8 by earlier phases and were not in what
Phase 8 shipped — and, unlike everything in the tables above, they were never
re-deferred with a reason. They are done now.

- **Bulk CSV import of students** — `POST /people/students/import/`.
  `apps/people/imports.py` reads the spreadsheet the school already has:
  headers matched case- and space-insensitively with the aliases an Excel file
  actually carries (`Surname`, `Sex`, `DOB`, `Parent Phone`), unknown columns
  ignored, level/class/programme/stream resolved by code. It calls
  `create_student`, `link_guardian` and `enrol_student` — the same services the
  API uses, so the numbering rules, the guardian matching and the capacity
  check all still run. The whole file is one transaction and every error names
  its row index, which is also why there is no dry-run flag: a failed import
  *is* the dry run. Each row additionally holds a savepoint, so a duplicate
  admission number is reported as a row rather than breaking the connection and
  taking the rest of the file's error reporting with it
- **Document verification** — `apps/common/verification.py` signs a token with
  `django.core.signing`, salted with the tenant slug so a token minted by one
  school does not verify at another. Nothing is stored: a register of issued
  documents grows forever and still cannot tell you the photocopy in your hand
  is the one it names. `GET /api/v1/public/verify/<slug>/<token>/` is public
  and unauthenticated because the person checking is an employer with a phone
  camera, and it answers `200 {"verified": false, "reason": ...}` rather than a
  4xx — a scanner that shows a browser error page has told the holder nothing.
  It confirms name, number, class and status, and nothing that the document
  does not already print
- **ID cards with QR** — `GET /people/students/{id}/id-card/` and
  `/people/students/id-cards/` for a whole class, CR80-sized, one card per
  page, off the same `xhtml2pdf` path as the report cards. The QR is signed per
  student, so moving a photograph between two cards in a batch does not make
  its code check out
- **Transcript authenticity QR** — the same stamp on
  `/assessment/transcript/<id>/?format=pdf`

`segno` is the one new dependency (pure Python, no dependencies of its own)
and its import is deferred exactly like `xhtml2pdf`'s: a server without it
still prints every document, with the verification URL in text instead of a
square.

One bug found on the way: `/reports/export/students/` named `other_names` on a
model whose field is `other_name`. A `StreamingHttpResponse` does not touch a
row until something consumes it, so the export had never raised in a test — it
would have raised in an office. There are now tests that consume the stream.

### Second pass: things declared and never wired

A sweep for the same failure mode — code that exists and nothing reaches —
found six more. Each was written, tested in isolation where it was testable at
all, and connected to nothing.

- **Administration API.** `admin.manage_users`, `admin.manage_roles`,
  `admin.manage_settings` and `admin.view_audit` had been in the catalogue
  since Phase 2 with nothing checking them, which by this codebase's own rule
  ("a permission that no code checks is a lie") made four of them lies.
  `UserListSerializer` and `RoleSerializer` were unused, and `assign_role` was
  called by nothing but its own test: **the only way to give a teacher their
  teacher role was the Django admin**, which needs a staff account on the
  school's own database. Now `/admin/users/`, `/admin/roles/` (with a
  `catalogue/` action, so a role editor offers the real codes rather than a
  free-text box), `/admin/audit/entries/` and `/settings/`. Role changes and
  settings changes are audited; a role may carry only permission codes the
  application actually checks, and a system role cannot be deleted
- **The credit minimum was never enforced.** `assert_minimum_credits` existed
  and `approve_registration` did not call it, so the rule `docs/domain.md`
  documents — checked at approval, not at entry — was documentation only.
  Approval now checks it, with `ignore_minimum` for the final-year student who
  legitimately has twelve units left
- **`Model.clean()` was unreachable from the API.** The timetable viewset
  checked clashes and then called `serializer.save()`, which does not run
  `full_clean()`. `POST /academics/timetable/` therefore accepted a period
  ending before it starts, and one belonging to neither a class arm nor a
  level, while the service refused both. Both paths now go through
  `validate_entry`, and Django's `ValidationError` is translated to an
  `AcademicError` so it is a 400 and not a 500
- **`require_nin` did nothing.** A school could tick the box and believe its
  records were complete. Enforced in `create_student`/`create_staff`, so the
  CSV import and the admissions conversion obey it too
- **`purge_expired_otps` was never scheduled.** An NDPR data-minimisation task
  with no beat entry and no caller: spent codes persisted forever. The
  retention promise is the schedule, not the function
- **`suspend()` / `reactivate()` were unreachable.** Suspending a school meant
  editing `status` in the Django admin, which leaves `suspended_at` and
  `suspension_reason` describing a suspension that is over. They are admin
  actions now, so the three fields move together

### Third pass: the same sweep, run to exhaustion

Three sweeps, each mechanical: extract every declaration of a kind, then grep
the rest of `apps/` and `config/` for it. Permission codes came back clean.
The other two did not.

- **`Idempotency-Key` was in `CORS_ALLOW_HEADERS` and read by nothing.** The
  Flutter outbox replays writes it could not send, which is at-least-once by
  construction: a request that times out *after* the server committed looks,
  from the phone, exactly like one that never arrived. Worse, the outbox id was
  minted only *after* the failure, so the replay could not have been recognised
  even if the server had been looking. `IdempotencyMiddleware` now claims the
  key, stores the response and replays it; the client mints the key before the
  first attempt and reuses it
- **`AuditAction.TENANT_SUSPENDED` was declared and never written**, while
  `apps/audit/models.py` opens by saying the public copy of the trail exists to
  record suspension. Cutting a whole school off the API was the platform's most
  consequential act and its least accountable — one rotating log line
- **`Plan.max_students` / `max_staff` were serialised to the pricing screen and
  enforced nowhere.** The `Plan` docstring's claim that "limits are enforced at
  the service layer" was the entire enforcement
- **`Domain.is_custom` / `verified_at` gated nothing.** A custom domain is typed
  into the admin before anyone confirms the school controls it, and it resolved
  from the moment it was saved
- **`StudentDocument.file` had no validation at all** — any extension, any size,
  served back from the application's own origin. `virus_scanned_at`, whose
  comment promised a Phase 9 scanner that Phase 9 shipped without, made it look
  guarded

Then four defects that were live code getting it wrong, not declarations
getting ignored:

- **A provider's interim delivery report counted as a failure.**
  `apply_delivery_report` treated anything it did not recognise as "delivered"
  as FAILED, and Termii sends `Sent`, `Pending` and `Accepted` on the way *to*
  a delivery. "We sent 412, 9 failed" counted how talkative the provider was
- **`sweep_pending` never gave up.** Nothing else in the system resolved a
  PENDING payment, so the half-hourly task re-asked the gateway about every
  abandoned checkout it had ever written, forever
- **`Invoice.recompute_paid` had no `else`.** A draft invoice is payable, so a
  bill paid at the counter before it was issued goes to PAID; reversing that
  payment left it PAID with the whole balance outstanding — and PAID is not
  payable, so the invoice was stuck
- **The gate terminal overruled the form teacher.** A pupil marked LATE by
  someone who saw them was reset to PRESENT by a biometric punch, which also
  moved the row without touching `version` — defeating the optimistic lock that
  `mark_bulk` exists to maintain

Two more, on the same trust boundaries: `rotate()` now refuses a deactivated
account (the API paths that deactivate revoke token families, the Django admin
does not), and the public document-verification endpoint is throttled like the
other three public endpoints.

`globally_unique_phone` was **deleted** rather than wired: it contradicted the
opening docstring of `apps/accounts/models.py`, which says per-tenant phone
uniqueness is deliberate because a parent with children in two schools is
normal.

### Fourth pass: states nothing enters, transitions nothing performs

The declaration sweeps were exhausted, so the fourth ran over the **status
enums**: for every `TextChoices` member, does any code path write it? Most
hits are user-chosen dropdown values (fee categories, payment methods,
relationships) and mean nothing. Two did not.

- **`EnrolmentStatus.WITHDRAWN` and `TRANSFERRED` had no writer**, because
  students had no `set_student_status`. Staff had `set_staff_status` carrying
  the login with the status since Phase 3; the student equivalent was never
  written, so `Student.status` was a plain serializer field and marking a
  leaver left their `Enrolment` on ACTIVE. ACTIVE enrolment is what four
  separate queries mean by "on the roll" — the invoice run billed them for next
  term, the promotion engine decided on them and re-enrolled them into the next
  session, and they held a class seat nobody could fill
- **`RegistrationStatus.REJECTED` and `SubjectRegistration.rejection_reason`
  had no writer either**, while three selectors and
  `SubjectRegistration.is_active` all read them: the entire read side was built
  for a state the write side could not produce. An adviser could approve and
  could not refuse, so saying no meant leaving the registration in SUBMITTED —
  which `assessment.selectors.COUNTED_STATUSES` scores anyway, making "no" and
  "not yet looked at" the same report card

Reading `approve_registration` for that turned up the matching hole on the
transition side: its own first sentence, *"Adviser first, then HOD"*, was
enforced by nothing. An HOD could finalise a DRAFT the student never
submitted, and either step could be applied to a DROPPED course, resurrecting
it into the credit count with `dropped_at` still set.

The sweep to keep: for every status enum, ask which members the *system* is
supposed to write, and grep for each. A state only ever read is a feature with
its write half missing.

### Fifth pass: docstrings that state a rule nothing keeps

The fourth pass ended by noting that `approve_registration` opened with a rule
it did not enforce. That is its own sweep: grep comments and docstrings for
assertive phrasing — *never*, *must*, *cannot*, *only ever*, *is refused* —
and check each claim against the code under it.

Most held, and the checking was worth it on its own: every CSV export really
does carry its own module's permission (`ExportView`'s claim that "an export
is never a way round object-level scoping"), NIN really is excluded from both
admins, derived money really is read-only in `InvoiceAdmin`, and
`override_window` really is gated on `assessment.publish_results` so a teacher
cannot edit a published mark. One did not.

- **"Rows schools must never truly lose"** — `SoftDeleteModel`'s opening line,
  repeated on `Student`. `soft_delete()` was written in Phase 1 and *no
  application code ever called it*, while `StudentViewSet` and `StaffViewSet`
  were plain `ModelViewSet`s. `DELETE /people/students/{id}/` reached
  `ModelViewSet.destroy` → `instance.delete()` → a real row deletion, taking
  every enrolment, attendance record, registration, result and guardian link
  with it by cascade. And where a pupil held an invoice, `Invoice.student` is
  `on_delete=PROTECT`, so the same request raised `ProtectedError` — an
  unhandled 500. `delete()` now means `soft_delete()` on both the instance and
  the queryset, with `hard_delete()` as the deliberate way out
- **`InvoiceAdmin` allowed deletion** while `PaymentAdmin` next to it blocked
  it with a reason, under a module docstring covering both with "money is
  never deleted". Now closed — the admin's bulk `delete_selected` bypasses the
  soft-delete override, so it had to be refused at the ModelAdmin

The sweep to keep: an assertive docstring is a test that was never written.
Grep for the phrasing, and for each claim find the line that enforces it.

### Sixth pass: cascades, and what a `DELETE` actually destroys

Two shapes left after the fifth. The first — every error code the API emits
against every code the Flutter client branches on — came back clean, and is
worth recording as clean: the client shows `message` directly for anything it
does not specially handle, so an unbranched code is by design, and the three
codes it branches on that no server literal matches (`TOKEN_NOT_VALID`,
`UNAUTHENTICATED`, `IDEMPOTENCY_IN_PROGRESS`) are all emitted, just not as
literals.

The second was the fifth pass's bug one level up, and larger. Every
academic-structure viewset subclasses `StructureViewSet`, a plain
`ModelViewSet`, so `DELETE /academics/sessions/{id}/` was routed — and
`Term.session`, `Enrolment.session` and `FeeStructure.session` are all
`CASCADE`, as is everything under a term. One request took the terms, the
enrolments, every subject registration, every score, every subject and term
result and the whole register. Where a bill happened to exist the same request
raised `ProtectedError` instead, because `Invoice.session` is `PROTECT`: an
unhandled 500 on the identical action, decided by whether the bursar had run
invoicing yet. The same shape sat under `EnrolmentViewSet` (a term's marks),
`AssessmentComponentViewSet` (`Score.component` is CASCADE, so deleting the
"Exam" column deleted every exam mark) and `FeeStructureViewSet`.

`apps.api.deletion.ProtectDependentsMixin` asks Django's own collector what a
delete would take and refuses with a 409 naming the counts. One **Django
subtlety** made the first version wrong in the most dangerous direction: a
related model with nothing cascading below it and no signal receivers is
*fast-deletable*, so the collector files a bare queryset under
`collector.fast_deletes` and never populates `collector.data`. `Score` is
exactly that shape — counting only `data` reported a mark-sheet column with
four hundred marks under it as unused. Any dependency check has to read both.

### Seventh pass: reading the modules the sweeps never opened

The mechanical sweeps were exhausted, so this one was ordinary reading of the
modules with the most consequence and the least prior attention. Three came
back clean and are recorded as such so nobody re-reads them looking for the
same thing: `people.numbering` (max+1 under a lock, and the concurrency was
already reasoned about in a `ponytail:` note), `numbering.msisdn` (the NCC
layer really does check status, `allows_user_accounts` *and* `nsn_length`),
and `auth_phone.services` (no enumeration, no plaintext at rest, the verify
limiter deliberately living in the cache so a rolled-back transaction cannot
reset it).

`common.fields` did not.

- **A secret the key could not decrypt was silently written back as blank.**
  `from_db_value` returned `""` on `InvalidToken` — reasonable on its own, and
  the comment said so — but the secrets share a row with ordinary settings, and
  both `TenantConfigurationAdmin` and `InstitutionSettingsSerializer` persist
  through `Model.save()`, which writes *every* column. So with the wrong key
  deployed — a rotation, a backup restored into the wrong environment, staging
  credentials in production — one edit to a school's crest replaced its
  gateway keys, SMS credentials and NIN with blanks that restoring the correct
  key could not undo. There was also no signal of any kind: no log, no error,
  no test. Now the read returns an `UNREADABLE` sentinel that still compares
  equal to `""` (so every "empty means unconfigured" caller is untouched) and
  `pre_save` refuses to persist it, loudly

Still declared and still doing nothing, deliberately — and now each says so in
the code, next to itself: `library.manage`, `hostel.manage` and
`finance.manage_payroll` (no such modules); `AuditAction.IMPERSONATION` (no
support tool to record); `Plan.modules` / `enabled_modules`,
`max_sms_per_month` and `billing_period_days` (the platform billing Phase 1
deferred to Phase 5 and Phase 5 never built); `MessageTemplate` as a whole (it
is stored, listed and edited, and no send path reads one); and
`virus_scanned_at` (nothing scans — the extension whitelist and size cap are
the whole of the protection).

### Eighth pass: class management, the write half nobody had built

The read side of class management was complete — `class_list`,
`arms_taught_by`, `/arms/{id}/students/`, the register, the mark sheet. The
write side stopped at `enrol_student`, which can seat a student only in the
instant the enrolment is created.

- **A promoted school had no way back into a classroom.**
  `apply_promotions` creates next session's enrolments with `class_arm=None`
  on purpose ("the school assigns arms itself; guessing one would put a pupil
  in a stream they never chose") — and then no code in the project could
  assign one. The whole institution moved up a year into no class at all, and
  the only route out was a PATCH on the enrolment, which wrote the foreign key
  and nothing else: no capacity check, no level check, and `Student.current_arm`
  left pointing at last year's class. That column is what attendance
  auto-marking, the student timetable, ID cards, SMS targeting and the CSV
  exports actually read, so the pupil showed up in one class's register and
  another's report.

`services.assign_to_arm` is now the only writer: it refuses a non-`ACTIVE`
enrolment, a retired arm, an arm belonging to another level (`ARM_MISMATCH`)
and an arm of the wrong stream (`STREAM_MISMATCH`), checks capacity, numbers
the seat, syncs `Student` — but only when the session is the current one, so
back-filling a closed year cannot rewind a pupil's position — and records the
move in the audit trail. `allocate_to_arm` does a whole year group in one
transaction, checking the batch against the seats before writing any of it.
`EnrolmentViewSet.perform_update` and `POST /arms/{id}/allocate/` both go
through it, and `GET /enrolments/unplaced/` is the worklist that follows a
promotion.

Two inert declarations went with it. `roll_number` had been settable since
Phase 1 and was sorted on by nothing — `class_list` now reads in register
order, unnumbered pupils last. `ClassArm.capacity` was enforced only at first
enrolment; it is now what `enrolled` / `seats_left` on the serialised arm are
measured against, so the office can see which class has room before it
allocates rather than after the refusal.

The client half is `mobile/lib/src/features/classes_screen.dart` (`/classes`,
dashboard tile gated on `academics.manage_enrolment`): the class register on
top of the allocation worklist, because seating an unplaced pupil and moving a
placed one are the same call with one id or forty. `EnrolmentSerializer` grew
`student_name`, `student_number` and `level_code` at the same time — without
them every row on both lists is three UUIDs.

Two client-side rules worth keeping: the worklist is filtered to the chosen
class's own level, so a placement the server would refuse with `ARM_MISMATCH`
cannot be selected at all; and `ARM_FULL` is the one refusal offered as a
question rather than reported as a failure, because a school that runs 45 to a
room of 40 should record the truth rather than edit the capacity to fit it.

### Ninth pass: dates the clock is never compared to

A new sweep shape, because the declaration sweeps had gone dry: for every
`DateField`/`DateTimeField` that is not `auto_now`, find the line that
compares it to `timezone.now()`. A date the system writes and never reads is
a deadline nobody keeps.

Most came back clean and are recorded so nobody re-runs them: `OtpRequest`
(`expires_at`, `invalidated_at`, `consumed_at` — all three checked in
`is_usable` and again in `verify_otp`), `Term.registration_*_at` /
`result_entry_*_at` (`RegistrationClosed`, `ScoreEntryClosed`),
`Announcement.expires_at` (`announcements_visible_to` filters it),
`Device.revoked_at` / `TokenFamily.revoked_at`, `Invoice.due_date` (no
`OVERDUE` status by design — `days_overdue` is computed on read, so an
invoice cannot get stuck in a status the clock has moved past). One did not.

- **Every trial was permanent.**
  `activate_trial` has set `trial_ends_at = now + plan.trial_days` since Phase
  1 — it is in the provisioning task's happy path, so every school on the
  platform has one — and no line of code anywhere compared it to the clock.
  `Plan.trial_days` was computed, stored, and shown on `/plans/`, and
  enforced by nothing. The two statuses that end a trial gave the same reading
  from the other side: `TenantStatus.PAST_DUE` and `ARCHIVED` were declared,
  `PAST_DUE` was even listed in `SERVABLE_STATUSES` as a state that still gets
  served — and nothing in the project could put a tenant into either. The only
  route out of `TRIAL` was a human opening the platform admin and choosing
  *Suspend*.

`tenants.expire_trials` (beat, 04:00 daily) is the missing half, in two steps
because the enum already described two. `services.lapse_trial` moves an
expired `TRIAL` to `PAST_DUE`, which is servable on purpose: a school whose
trial ran out on a Friday keeps working while somebody sorts out payment, and
the platform trail records it (`platform.tenant.trial_lapsed`). Only after
`TENANT_PAST_DUE_GRACE_DAYS` (14) does the same task call the existing
`suspend`, so no school is ever cut off on the run that first notices.

Two boundaries worth keeping. The grace window is measured from
`trial_ends_at` and the query requires it non-null, so a future billing module
that writes `PAST_DUE` for a *renewal* will be skipped rather than suspended
on a date it never set — it must bring its own. And nothing here touches
`ACTIVE`: `reactivate` is still the way back, and a reactivated school with a
long-past `trial_ends_at` is not swept up again.

Still declared and still inert, deliberately: `TenantStatus.ARCHIVED` (there
is no retention policy to archive against, and inventing one silently is how
a school's records disappear) and `Plan.billing_period_days` — a trial ending
is not a subscription renewing, and this pass built only the first.

### Tenth pass: terminal states, rebuilds that only add, and the ledger's edges

Five bugs, from four sweep shapes. Two of them are the same shape read from
opposite ends: a state machine with a state nothing should leave, and a
"rebuild" that only ever wrote.

- **A reversal could be undone by a webhook.** `confirm_payment` returned early
  for `SUCCESS` only, so `REVERSED` was a state the gateway could walk a
  payment back out of. Every route in reached it: a gateway retries its webhook
  for a day, `sweep_pending` re-verifies, and `POST /payments/{id}/verify/`
  carries nothing but `IsAuthenticated`. A bounced settlement reversed by the
  bursar on Monday came back to `SUCCESS` on Tuesday, `recompute_paid` credited
  the money a second time, and the row was left reading `SUCCESS` with a
  `reversal_reason` still on it. `FAILED` stays rescuable on purpose — an
  abandoned checkout that really did settle is a payment we want, which is what
  `test_abandoning_is_not_final` fixes in place. `REVERSED` is a decision
  somebody made, and this is the one path that could quietly reverse it.

- **`recompute_term` wrote every row it computed and removed none.** The
  docstring said "a rebuild, not an increment"; it was half a rebuild. When
  `drop_subject` or `reject_registration` moves a registration out of
  `COUNTED_STATUSES`, the `SubjectResult` it had already earned stayed behind —
  and `broadsheet`, `report_card_context` and `transcript_context` all filter on
  `registration__term` alone, so a course the pupil no longer takes went on
  printing on their report card. `transcript_context` promised the opposite in
  as many words: "withdrawn or dropped courses never appear, because they never
  produced a SubjectResult". Worse, `publish_results` counts `is_complete=False`
  over the same unfiltered set, and `enter_scores` refuses a dropped
  registration with `NOT_REGISTERED` — so the marks could never be completed and
  `force` became the only way a term was ever published again. `_prune_stale_
  results` is the missing half; a `TermResult` goes when the enrolment has no
  counted registration left at all, because otherwise a pupil who dropped
  everything kept an average, a position and a promotion decision that
  `results_visible_to` showed to their parent.

- **`Idempotency-Key` compared the body and nothing else.** `method` and `path`
  were columns on `IdempotencyRecord`, written on every claim and read by no
  line of code — the declared-but-inert shape the second and third passes were
  built around, hiding inside the fix for a different bug. Most write endpoints
  here are `@action`s that take no body at all (`invoices/{id}/issue/`,
  `payments/{id}/verify/`, `terms/{id}/publish/`), so one key sent to two of
  them carried byte-identical bodies, passed the reuse check, and got the
  *first* endpoint's stored response back. The test that proves it is worth
  reading for the failure mode alone: without the fix, a `POST` to
  `/academics/terms/` answers **201 Created** with an academic session's body.
  The client is told it succeeded and the term was never written. The fingerprint
  is now `method \0 path \0 body`.

- **A cached empty body crashed the offline fallback.** `ApiClient.get` guards
  the online decode (`body.isEmpty ? null : jsonDecode(body)`) because an empty
  response really does happen — and then cached that same empty string and fed
  it to a bare `jsonDecode` on the offline path, where a `FormatException` is a
  crash on the one code path that exists to avoid one. Empty bodies are no
  longer cached, and the read rechecks.

- **CSV exports shipped live formulas.** `csv_response` wrote every cell
  verbatim, and the module docstring invites the payload: "a school that wants
  formatting opens it in Excel and saves". Names, notes and admission numbers
  arrive from an admissions form and a CSV import, so a pupil registered as
  `=HYPERLINK(...)` — or the classic `=cmd|'…'!A1` — is a formula aimed at
  whoever opens the bursar's export. Quoting does not help: a CSV-quoted cell is
  still a formula once parsed. Cells that begin `=`, `+`, `-`, `@`, tab or CR
  now get a leading apostrophe, and nothing else is touched so the numbers and
  dates the office sorts on still sort.

**Clean under this pass, do not redo:** `auth_phone.ratelimit` (`clear_lockout`
resets `otp_verify_attempts`, so a successful login does not spend the next
one's allowance); `tenants.db.create_database` (the SQLite template copy
correctly skips `migrate` only for a database it just created);
`attendance.mark_bulk` scoping (both sides of the `allowed_student_ids`
comparison are `str`); `TenantRouter._db` for all three app-split cases.

### Eleventh pass: two flags that describe one "now"

**Same shape as the eighth pass — a complete read side is not evidence of a
write side, and here the write side existed but was only half of one.**

- **The current session and the current term could name different years.**
  `AcademicSession.is_current` and `Term.is_current` are separate booleans, each
  with its own `save()` clearing the others of its own model, and nothing tying
  the pair together. Both are read constantly and by different halves of the
  system: `current_term()` keys score entry, course registration, the timetable
  and `my-classes`, while `session__is_current` keys enrolment, class lists,
  reinstatement and invoicing. So marking next year current — the one thing a
  school does every September — left last year's term current, and the school
  ran in two academic years at once: this year's marks written against a closed
  term, in a session whose enrolments they do not belong to.

  There was also no *route* for it beyond `PATCH {"is_current": true}` on
  reference data, which wrote the column, recorded nothing in the audit trail
  and left the second flag to memory.

  The invariant now lives in both `save()` methods, so seeds, the admin, a
  management command and the API all obey it: promoting a session demotes any
  current term outside it, and promoting a term promotes the session that holds
  it. `services.set_current_session` adds the half a bare column write cannot —
  it picks the replacement term by the calendar (the term containing today, else
  the session's first) and writes the audit entry. A session with no terms yet
  ends with no current term at all, which is what the screens already handle;
  a term in the wrong year is not.

  `POST /academics/sessions/{id}/set-current/` and
  `POST /academics/terms/{id}/set-current/`, both behind
  `academics.manage_structure`.

- **`Model.clean()` does not run under DRF, and both models kept their date rule
  there.** `AcademicSession.clean` and `Term.clean` refuse `end_date <=
  start_date`; the admin honours it and every `POST`/`PATCH` walked past it, so
  the office could file a term that ended before it began — after which every
  window check (`accepts_scores`, `accepts_registration`) and every date lookup
  reads it as closed with nothing to show why. Now enforced in the serializers,
  which is also where a PATCH sending one bound gets it compared against the
  stored other.

**The client half, same pass.** An endpoint nobody can reach is the read-side
bug one level up, so the calendar went onto `/structure` — already the
`manage_structure` screen for reference data, and already arguing in its own
docstring that a second tile for the same visit is not worth it. Sessions and
the current session's terms list above the levels, each row carrying either a
**Current** chip or a **Make current** button, never both. The button confirms
first, naming what moves and what does not ("marks already entered stay where
they are"), because it changes what every teacher in the school sees. Dates are
picked, never typed — a period that ends before it begins is now refused server
side, and a picker cannot produce one. Neither `set-current` call is ever queued
offline: an outbox replay a week later would drag the school back into a term it
has since left. The tile is "Calendar & structure" now; advancing a term is the
one thing on that screen a school does every term rather than once.

**The year picker, same screen.** The terms list started out pinned to the
current session, which left next year's terms unkeyable until the school had
already rolled into it — and a school keys next year's calendar in July. A
"Showing" dropdown above the list now switches years. Two details worth keeping:
the picked year is held as **null** for "whichever is current", so a rollover
moves the list with the school instead of stranding it on the year that happened
to be current when the screen opened; and a picked year that has vanished under
the screen falls back to the current one (`sessionInView`), because an empty term
list reads as "none keyed" and would invite a second set. Advancing to a term of
a year the school is not in says so in the confirmation — the server promotes
that term's session with it, which is a bigger move than the button suggests.
