# Core domain

## One structure, two institution types

`SECONDARY` and `TERTIARY` share the same tables. The alternative — parallel
models — would double every join, serializer, report and PDF template for the
sake of vocabulary that `labels_for()` already resolves.

| Concept | Secondary | Tertiary |
|---|---|---|
| `AcademicSession` | 2025/2026 | 2025/2026 |
| `Term` | 3 terms | 2 semesters |
| `ClassLevel` | JSS1 … SSS3 | 100 … 500 Level |
| `ClassArm` | SSS2 Gold | unused |
| `Stream` | Science / Arts / Commercial | unused |
| `Faculty` → `Department` → `Programme` | unused | Science → CSC → ND Computer Science |
| `Subject` | Mathematics | CSC201, 3 credit units |
| `Enrolment` | pupil in SSS2 Gold | student at 200 Level on ND CSC |
| `SubjectRegistration` | subjects offered | course registration with approval |

Columns that apply to one type only are nullable. Provisioning seeds the right
levels and streams for the type the school signed up as.

## Registration rules

Secondary and tertiary run the same code path with different outcomes,
because a JSS1 pupil offering Mathematics needs no course adviser:

- **No programme** (secondary) → registration is `APPROVED` on creation.
- **Programme** (tertiary) → `SUBMITTED` → `ADVISER_APPROVED` → `APPROVED`.

Enforced on the way in:

| Rule | Behaviour |
|---|---|
| Credit maximum | Rejected at registration; the batch is atomic |
| Credit minimum | Checked at approval, not at entry — a student may save a partial semester. `ignore_minimum` on the approve action is for the final-year student with twelve units left to graduate |
| Prerequisites | Refused with the missing course codes in the message |
| Semester offered | A second-semester course cannot be taken in the first |
| Stream | An SSS Arts pupil cannot register a Science-only subject |
| Duplicate | Same course twice in a term is refused |
| Add/drop window | `Term.registration_opens_at` / `_closes_at`; null means open |
| Carry-over | A course registered before is flagged automatically |

A batch that fails any row registers nothing. A half-registered semester is a
support call.

The approval order above is **enforced**, not just documented: the adviser step
requires `SUBMITTED` and the HOD step requires `ADVISER_APPROVED`, so an HOD
cannot finalise a draft the student never submitted. `DROPPED` and `REJECTED`
are terminal — approving one would resurrect it into the credit count and onto
the report card with `dropped_at` still set. Re-register instead. Approving an
already-`APPROVED` registration is a no-op, not an error.

`POST /academics/registrations/{id}/reject/` is the other half, and it requires
a reason. Without it the only way to refuse a course was to leave it sitting in
`SUBMITTED` — which `assessment.selectors.COUNTED_STATUSES` scores anyway, so
"no" and "not yet looked at" produced the same report card.

## Object-level scoping

Database isolation keeps school A out of school B. Scoping is the second layer,
inside one school:

```
people.selectors.students_visible_to(user)
  has people.view_student  → everyone
  teacher                  → students in arms/levels they are assigned to,
                             plus their whole form class
  guardian                 → their wards only
  student                  → themselves only
  anyone else              → nothing
```

This filters the **queryset**, not the response. A list endpoint that filtered
afterwards would still leak row counts and pagination cursors.

## Staff records and logins

A `Staff` row is a personnel file. A `User` is a login. They are separate
because most of the people in a school's records never sign in, and the ones
who do are matched by phone number — a teacher who is also a parent here is one
account holding both.

`POST /api/v1/people/staff/{id}/account/` is the only thing that joins them:

```
staff.phone → accounts.get_or_create_user()  (matched, never duplicated)
             → staff.user = user
             → accounts.set_roles(user, roles)   (audited: before and after)
```

Two permissions, deliberately not one:

| Permission           | What it buys                                    |
| -------------------- | ----------------------------------------------- |
| `people.view_staff`  | Read the staff list, see who has no login yet   |
| `people.manage_staff`| Create and edit the personnel file              |
| `admin.manage_users` | Decide who may sign in, and with which roles    |

So the office clerk who keeps HR records straight cannot thereby make
themselves principal. No credential is ever set by an administrator: the holder
signs in with an OTP to their own number and chooses their own PIN, because an
administrator who could set someone's PIN could sign in as them.

Until a staff record has a `user`, `people.selectors.staff_for()` finds nothing
for that person — which means no "my classes", no register, and no name against
an admissions decision. That is why the staff list marks the gap.

## Leaving

Status is never a plain field write on either side. Both go through a service,
because in both cases the status is only half the change:

- `set_staff_status(EXITED)` disables the login and revokes every refresh
  family. An exit that leaves the account alive is a teacher who handed back
  the keys and can still mark a register.
- `set_student_status(WITHDRAWN | TRANSFERRED | GRADUATED)` closes the live
  `Enrolment` to the matching state and stamps `date_left`. An exit that leaves
  the enrolment `ACTIVE` is worse than cosmetic: an ACTIVE enrolment is what
  `finance.cohort_for` bills, what `decide_promotions` decides on, and what
  `enrol_student` counts against class capacity — so a departed pupil got next
  term's invoice, a promotion decision, and a seat nobody else could fill.

`SUSPENDED` is deliberately not a departure on either side: a suspension ends,
and the pupil keeps their place and their bill. Only `WITHDRAWN` and
`TRANSFERRED` reopen on reinstatement — a graduate coming back is a fresh
admission, the same reasoning that stops `set_staff_status` restoring roles
automatically. Past `PROMOTED` and `REPEATED` enrolments are never touched:
they are the history of previous sessions.

`status = EXITED` closes the login and revokes every refresh family, so the
teacher who hands back the keys is not still holding a session that can mark a
register. Reinstatement reopens the login but does not restore roles: someone
returning in a different post is granted that post deliberately.

## Timetable clash detection

Three resources can collide: teacher, room, class. Overlap is
`start < other.end AND end > other.start`, so 10:00–11:00 following
09:00–10:00 is back-to-back, not a clash. `POST
/academics/timetable/check_clash/` is a dry run that returns what a proposed
period would hit and why.

Not a database constraint: overlap across three different resources is not
expressible as one unique index.

## Attendance and the offline teacher

The design case is a teacher marking forty pupils in a staffroom with no
signal, syncing an hour later.

- `POST /attendance/students/bulk/` — the whole class in **one** request, one
  transaction.
- Each row carries the `version` the device last saw. If the server has moved
  on, that row comes back as a `CONFLICT` instead of silently overwriting —
  so an office correction survives a late sync.
- Any failed row rolls the batch back and every error names its row index and
  student.
- `recorded_at` is when the teacher tapped; `created_at` is when the phone
  found signal. Reports use `recorded_at`.

One row per student per day per subject; `subject` is null for the daily
register, so a per-period register and a form register coexist without
colliding.

### Biometric gate terminals

Devices POST to `/attendance/biometric/<device_code>/`, unauthenticated (a
fingerprint reader holds no session) but **HMAC-signed** over the raw body
with that device's secret. Unknown device and bad signature give the same 401,
so the endpoint cannot be probed. Ingestion is idempotent on
`(device, external_id, occurred_at)` — terminals retry hard on a flaky LAN.
Unmatched punches are kept: an unenrolled thumb at the gate is exactly what an
administrator needs to see.

**A hand mark outranks the gate.** A pupil recorded LATE or EXCUSED by someone
who saw them keeps that status: the turnstile knows strictly less than the
teacher. A punch that *does* change a row bumps `version`, so the offline
client's optimistic lock reports the conflict instead of overwriting silently.
Only `alive()` records match — a departed pupil's finger stays enrolled on the
terminal long after the record is soft-deleted, and should not resurrect them
into today's register.

## Assessment

Three things are data, not code, because every school disagrees about them:

| Configured | Example |
|---|---|
| Mark-sheet columns (`AssessmentComponent`) | CA1 20, CA2 20, Exam 60 — or 30/70, or a per-subject 40/60 for a practical |
| Grading scale (`GradingScale` / `GradeBand`) | WAEC A1…F9, or a 5-point CGPA scale; a different scale per level if wanted |
| Pass mark | 40 in most schools, 50 in some. Decides the grade *and* the promotion |

There is no weighting field: the weight **is** the maximum score, which is how
schools already think. Components resolve most-specific-first — subject, then
level, then the school default — and do not merge, so a practical subject that
declares its own columns does not inherit a stray exam column.

`SubjectResult` and `TermResult` are derived tables, rebuilt by
`recompute_term` and never edited by hand. Recomputing is idempotent, which is
what makes a correction safe two weeks after results day.

```
scores ─► SubjectResult (total, %, grade, point, is_complete)
             ├─ position within the cohort, class average / highest / lowest
             └─► TermResult (average, GPA, CGPA, position, attendance)
                    └─► promotion decision
```

A student is ranked against their **arm** in a secondary school, and against
their **level and programme** in a tertiary one: a 200-level Computer Science
student is not in competition with 200-level Accountancy. Ties share a
position — 1, 2, 2, 4 — so the sheet still agrees with the class size.

GPA is null where there are no credit units. A JSS1 report card prints the
average instead; printing 0.00 would be a lie.

### Publishing

`Term.results_published_at` is the switch. Until it is set, a student or
guardian sees nothing, and publishing is refused while any counted
registration is missing a mark. `force` exists for the real case of a subject
with no exam, and it is audited.

A teacher cannot edit a published result; an exams officer can, and every
correction bumps the score's `version`.

### Promotion

Decide, review, then apply — three steps, because the second one is where an
exams officer overrides the three rows they disagree with:

```
decide_promotions(session)   → PROMOTE / REPEAT / GRADUATE per enrolment
override_promotion(...)      → audited, with a reason
apply_promotions(session, next_session)
     PROMOTE  → next level, next session, no arm (the school assigns arms)
     REPEAT   → same level, next session
     GRADUATE → student closed out with a leaving date
```

Applying twice is a no-op: nothing is ACTIVE in the old session any more.

### Printed documents

Report card, broadsheet and transcript are Django templates rendered by
`xhtml2pdf` — a templating problem, not a drawing problem, and no browser or
GTK stack in the container. Every noun on them comes from `labels_for()`, so
the same template prints "Report card / Term / Subject" for a school and
"Result slip / Semester / Course" for a polytechnic.

### Verification

A transcript, a result slip and an ID card leave the building and come back as
a photocopy. Each carries a QR of

```
/api/v1/public/verify/<school>/<token>/
```

The token is `django.core.signing` over `(document kind, object id, issue
date)`, salted with the school's slug. Nothing is stored: a register of issued
documents grows forever and still cannot tell you the sheet in your hand is
the one it names — a signature can, and it survives a database restore.

The endpoint is public and unauthenticated because the person checking is an
employer or a gateman with a phone camera, and the token off the document is
the credential. It answers `200 {"verified": false, "reason": ...}` rather
than a 404, since a scanner that shows a browser error page has told the
holder nothing. It confirms name, number, class and status — and nothing the
document does not already print, so it cannot be used as a lookup service.

Rotating `DJANGO_SECRET_KEY` invalidates every document already printed.

## Bulk intake

Every school arrives with a spreadsheet, so `POST /people/students/import/`
takes it: headers matched case-insensitively with the aliases a real Excel
file carries, unknown columns ignored, classes and programmes resolved by
code. It calls the same services the API does — the numbering rules, guardian
matching and capacity check all still run.

One transaction, and every error names its row index, so the office fixes line
five of their file. There is deliberately no dry-run flag: a failed import
*is* the dry run, because it writes nothing.

## Sync primitives

| Parameter | Effect |
|---|---|
| `?updated_since=<iso8601>` | Delta pull instead of a full list |
| `If-None-Match` | 304 when the list has not changed |
| `?expand=student,subject` | Nested objects instead of forty follow-up calls |

The ETag is derived from `(row count, max updated_at)` — one aggregate query,
not a serialised page.

## Deletion

`Student`, `Staff` and `Invoice` extend `SoftDeleteModel`, and on those
`delete()` **means** `soft_delete()` — both on the instance and on a queryset.
A `DELETE` over the API answers 204 and stamps `deleted_at`; the row, and
everything cascading off it, stays. That matters most for what a hard delete
used to take with it: a pupil's enrolments, register, registrations, results
and guardian links are all `CASCADE`.

`hard_delete()` is the deliberate way out, for a data-protection erasure. The
Django admin's bulk `delete_selected` is the other, because it builds a
collector and never calls `Model.delete()` — which is why `InvoiceAdmin` and
`PaymentAdmin` both refuse deletion outright. Money is cancelled, waived or
reversed, never removed.

Soft-deleted rows still come back from `?updated_since=` with `deleted_at`
set, so an offline client learns to drop them locally.

Reference data is the other half. A session, term, class level, enrolment,
assessment component or fee structure is one row with an academic year
pointing at it by `CASCADE`, so deleting one used to take the terms,
enrolments, registrations, scores, results and register with it — or raise
`ProtectedError` as a 500 where an invoice happened to exist.
`apps.api.deletion.ProtectDependentsMixin` now asks Django what a delete would
actually destroy and answers **409 `DEPENDENTS_EXIST`**, naming the counts:

```json
{"error": {"code": "DEPENDENTS_EXIST",
           "details": {"dependents": {"terms": 3, "enrolments": 412}}}}
```

`SET_NULL` relations are not dependents — nulling `ClassArm.stream` loses
nothing. Most of these models carry `is_active`, which is what "we no longer
offer this" should mean.

## Number formats

Configurable per tenant via `TenantConfiguration.label_overrides`:

```
{yy} {yyyy} {dept} {prog} {level} {serial}

{dept}/{yy}/{serial}   →  CSC/25/0042
KC/{yyyy}/{serial}     →  KC/2025/0042
```

Serials count within the prefix, so they restart per department per year.
Matriculation numbers are issued once and never reissued — they are on the
transcript.
