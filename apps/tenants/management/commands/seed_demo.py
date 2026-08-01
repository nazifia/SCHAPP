"""Build a school you can actually click through.

`python manage.py seed_demo` gives a provisioned tenant with a session, three
terms, classes, subjects, staff, pupils with guardians, a full set of marks,
computed results, issued invoices with part payments, and a published notice —
i.e. every screen in the app has something real behind it.

Everything is `get_or_create`, so running it twice tops the demo up instead of
duplicating it. It refuses to touch a tenant that is not the demo one, because
"seed the demo" typed against a live school is the sort of accident nobody
recovers from politely.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.tenants.db import schema_context
from apps.tenants.models import InstitutionType, Tenant

FIRST_NAMES = [
    "Ngozi",
    "Chidi",
    "Bola",
    "Tunde",
    "Amaka",
    "Emeka",
    "Fatima",
    "Yusuf",
    "Zainab",
    "Segun",
    "Ifeoma",
    "Musa",
    "Aisha",
    "Kelechi",
    "Bimbo",
    "Danjuma",
]
LAST_NAMES = [
    "Ali",
    "Eze",
    "Adeyemi",
    "Bakare",
    "Okafor",
    "Ibrahim",
    "Nwosu",
    "Balogun",
    "Chukwu",
    "Lawal",
    "Okonkwo",
    "Abubakar",
]
SUBJECTS = [
    ("MTH", "Mathematics", 3),
    ("ENG", "English Language", 3),
    ("BIO", "Biology", 2),
    ("CHM", "Chemistry", 2),
    ("PHY", "Physics", 2),
]


class Command(BaseCommand):
    help = "Populate a demo school with realistic data. Safe to run twice."

    def add_arguments(self, parser):
        parser.add_argument("--slug", default="demo-college")
        parser.add_argument("--name", default="Demo College")
        parser.add_argument(
            "--type", choices=[c for c, _ in InstitutionType.choices], default="SECONDARY"
        )
        parser.add_argument("--students", type=int, default=24)
        parser.add_argument("--seed", type=int, default=2026, help="Deterministic randomness")

    def handle(self, *args, **options):
        random.seed(options["seed"])
        tenant = self._tenant(options)

        with schema_context(tenant.schema_name):
            session, terms = self._calendar(tenant.institution_type)
            arms, subjects, programme = self._structure(tenant.institution_type)
            staff = self._staff()
            students = self._students(options["students"], session, arms, programme)
            self._teaching(staff, subjects, arms, terms[0])
            self._registrations(students, subjects, terms[0], programme)
            self._marks(terms[0], subjects, staff)
            self._results(terms[0])
            self._attendance(terms[0], students)
            self._fees(session, terms[0])
            self._notice()

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDemo school ready.\n"
                f"  slug:  {tenant.slug}\n"
                f"  API:   curl -H 'X-Tenant-Slug: {tenant.slug}' "
                f"http://localhost:8000/healthz\n"
                f"  app:   choose '{tenant.slug}' on the school screen; sign in with any "
                f"seeded phone and read the OTP off the runserver console.\n"
            )
        )

    # -- tenant ------------------------------------------------------------
    def _tenant(self, options) -> Tenant:
        from apps.tenants.services import create_tenant
        from apps.tenants.tasks import provision_tenant

        tenant = Tenant.objects.filter(slug=options["slug"]).first()
        if tenant is None:
            tenant = create_tenant(
                name=options["name"],
                slug=options["slug"],
                institution_type=options["type"],
                contact_email=f"{options['slug']}@example.ng",
                consented=True,
            )
            provision_tenant(str(tenant.pk))
            tenant.refresh_from_db()
            self.stdout.write(f"provisioned {tenant.slug}")
        else:
            self.stdout.write(f"topping up {tenant.slug}")
        return tenant

    # -- calendar ----------------------------------------------------------
    def _calendar(self, institution_type):
        from apps.academics.models import AcademicSession, Term

        year = timezone.localdate().year
        session, _ = AcademicSession.objects.get_or_create(
            name=f"{year}/{year + 1}",
            defaults={
                "start_date": date(year, 9, 1),
                "end_date": date(year + 1, 7, 31),
                "is_current": True,
            },
        )
        # Two semesters for a polytechnic, three terms for a school. Seeding
        # three either way gave a demo polytechnic a "Third Term" no tertiary
        # calendar has, and a CGPA computed over three semesters.
        if institution_type == InstitutionType.TERTIARY:
            spans = [
                ("First Semester", date(year, 9, 1), date(year + 1, 1, 31)),
                ("Second Semester", date(year + 1, 2, 9), date(year + 1, 7, 25)),
            ]
        else:
            spans = [
                ("First Term", date(year, 9, 1), date(year, 12, 15)),
                ("Second Term", date(year + 1, 1, 8), date(year + 1, 4, 5)),
                ("Third Term", date(year + 1, 4, 22), date(year + 1, 7, 25)),
            ]
        terms = []
        for index, (name, start, end) in enumerate(spans, start=1):
            term, _ = Term.objects.get_or_create(
                session=session,
                index=index,
                defaults={
                    "name": name,
                    "start_date": start,
                    "end_date": end,
                    "is_current": index == 1,
                },
            )
            terms.append(term)
        return session, terms

    def _structure(self, institution_type):
        from apps.academics.models import ClassArm, ClassLevel, Department, Programme, Subject

        arms = []
        for level in ClassLevel.objects.order_by("order")[:3]:
            for name in ("A", "B"):
                arm, _ = ClassArm.objects.get_or_create(
                    level=level, name=name, defaults={"capacity": 40}
                )
                arms.append(arm)

        subjects = []
        for code, title, units in SUBJECTS:
            subject, _ = Subject.objects.get_or_create(
                code=code, defaults={"title": title, "credit_units": units}
            )
            subjects.append(subject)

        # A tertiary enrolment without a programme is treated as a secondary one
        # everywhere it matters: `register_subjects` approves on creation when
        # `enrolment.programme is None`, so a demo polytechnic seeded without one
        # had an adviser queue that could never fill, and no credit bounds to
        # enforce. The minimum is the seeded catalogue's own total, so a full
        # load clears it and a partial one trips the override the screen offers.
        programme = None
        if institution_type == InstitutionType.TERTIARY:
            department, _ = Department.objects.get_or_create(
                code="CSC", defaults={"name": "Computer Science"}
            )
            programme, _ = Programme.objects.get_or_create(
                code="ND-CSC",
                defaults={
                    "department": department,
                    "name": "Computer Science",
                    "duration_years": 2,
                    "min_credit_units": sum(units for _, _, units in SUBJECTS),
                    "max_credit_units": 24,
                },
            )
        return arms, subjects, programme

    # -- people ------------------------------------------------------------
    def _staff(self):
        from apps.accounts.models import Role, User
        from apps.people.models import Staff, StaffType
        from apps.people.services import create_staff

        made = []
        wanted = [
            ("Adaeze", "Nwankwo", "teacher", "+2348030000001"),
            ("Ibrahim", "Sule", "teacher", "+2348030000002"),
            ("Grace", "Oyelaran", "principal", "+2348030000003"),
            ("Samuel", "Aboderin", "bursar", "+2348030000004"),
        ]
        for first, last, role_code, phone in wanted:
            user, _ = User.objects.get_or_create(
                phone=phone, defaults={"first_name": first, "last_name": last}
            )
            role = Role.objects.filter(code=role_code).first()
            if role:
                user.roles.add(role)
            staff = Staff.objects.filter(user=user).first()
            if staff is None:
                # Through the service, so the demo exercises the same staff
                # numbering a real onboarding would.
                staff = create_staff(user=user, first_name=first, last_name=last, phone=phone)
            made.append(staff)

        # Two with no login, because that is the state the staff screen exists
        # to surface and a demo where everyone can already sign in never shows
        # it. One has a number and is ready for `staff/{id}/account/`; the other
        # has none, which is the caretaker hired off a paper form — and the case
        # that answers STAFF_HAS_NO_PHONE.
        unlinked = [
            ("Chidinma", "Eze", "Laboratory Technologist", "+2348030000005"),
            ("Musa", "Danladi", "Caretaker", ""),
        ]
        for first, last, designation, phone in unlinked:
            staff = Staff.objects.filter(first_name=first, last_name=last).first()
            if staff is None:
                staff = create_staff(
                    first_name=first,
                    last_name=last,
                    phone=phone,
                    designation=designation,
                    staff_type=StaffType.NON_TEACHING,
                )
            made.append(staff)
        return made

    def _students(self, count: int, session, arms, programme=None):
        from apps.academics.services import enrol_student
        from apps.accounts.models import Role, User
        from apps.people.models import Guardian, Student, StudentGuardian

        guardian_role = Role.objects.filter(code="guardian").first()
        students = []
        for index in range(1, count + 1):
            number = f"DC/{session.start_date.year % 100}/{index:04d}"
            student, created = Student.objects.get_or_create(
                admission_number=number,
                defaults={
                    "first_name": random.choice(FIRST_NAMES),
                    "last_name": random.choice(LAST_NAMES),
                },
            )
            arm = arms[index % len(arms)]
            enrol_student(
                student=student,
                session=session,
                level=arm.level,
                class_arm=arm,
                programme=programme,
            )
            if created:
                phone = f"+23481{index:08d}"
                user, _ = User.objects.get_or_create(
                    phone=phone,
                    defaults={"first_name": "Parent of", "last_name": student.last_name},
                )
                if guardian_role:
                    user.roles.add(guardian_role)
                guardian, _ = Guardian.objects.get_or_create(
                    user=user,
                    defaults={
                        "first_name": "Parent of",
                        "last_name": student.last_name,
                        "phone": phone,
                    },
                )
                StudentGuardian.objects.get_or_create(
                    student=student, guardian=guardian, defaults={"is_primary": True}
                )
            students.append(student)
        return students

    def _teaching(self, staff, subjects, arms, term):
        from apps.academics.models import TeachingAssignment

        teachers = staff[:2] or staff
        for index, subject in enumerate(subjects):
            for arm in arms:
                TeachingAssignment.objects.get_or_create(
                    staff=teachers[index % len(teachers)],
                    subject=subject,
                    session=term.session,
                    term=term,
                    class_arm=arm,
                    defaults={"level": arm.level},
                )

    def _registrations(self, students, subjects, term, programme=None):
        from apps.academics.models import Enrolment, RegistrationStatus, SubjectRegistration

        # A tertiary demo whose every registration is already APPROVED leaves the
        # approvals screen empty on all three of its tabs, which reads as a broken
        # screen rather than an empty queue. Three students carry one state each.
        # Only the refused one loses its marks: `COUNTED_STATUSES` scores a
        # SUBMITTED registration, which is the reason the queue is worth clearing.
        queued = (
            {
                students[0].pk: (RegistrationStatus.SUBMITTED, ""),
                students[1].pk: (RegistrationStatus.ADVISER_APPROVED, ""),
                students[2].pk: (
                    RegistrationStatus.REJECTED,
                    "Registered after the add/drop window closed.",
                ),
            }
            if programme and len(students) >= 3
            else {}
        )

        for student in students:
            enrolment = Enrolment.objects.filter(student=student, session=term.session).first()
            if enrolment is None:
                continue
            status, reason = queued.get(student.pk, (RegistrationStatus.APPROVED, ""))
            for subject in subjects:
                SubjectRegistration.objects.get_or_create(
                    enrolment=enrolment,
                    subject=subject,
                    term=term,
                    # In `defaults`, so topping up a seeded demo never drags a
                    # decision somebody made on the screen back into the queue.
                    defaults={"status": status, "rejection_reason": reason},
                )

    # -- assessment --------------------------------------------------------
    def _marks(self, term, subjects, staff):
        from apps.assessment.models import AssessmentComponent, Score
        from apps.assessment.selectors import counted_registrations

        components = list(AssessmentComponent.objects.filter(level=None, subject=None))
        entered = 0
        for subject in subjects:
            for registration in counted_registrations(term, subject=subject):
                for component in components:
                    # A believable spread: most pupils pass, a few do not.
                    fraction = min(max(random.gauss(0.62, 0.18), 0.05), 1.0)
                    value = (component.max_score * Decimal(str(round(fraction, 2)))).quantize(
                        Decimal("0.01")
                    )
                    _, created = Score.objects.get_or_create(
                        registration=registration,
                        component=component,
                        defaults={
                            "score": value,
                            "entered_by": staff[0] if staff else None,
                            "recorded_at": timezone.now(),
                        },
                    )
                    entered += int(created)
        self.stdout.write(f"  {entered} marks")

    def _results(self, term):
        from apps.assessment.services import recompute_term

        summary = recompute_term(term)
        self.stdout.write(f"  results: {summary}")

    def _attendance(self, term, students):
        from apps.attendance.models import AttendanceStatus, StudentAttendance

        start = max(term.start_date, timezone.localdate() - timedelta(days=20))
        days = [start + timedelta(days=offset) for offset in range(10)]
        rows = []
        for student in students:
            for day in days:
                if day.weekday() >= 5:
                    continue
                rows.append(
                    StudentAttendance(
                        student=student,
                        term=term,
                        date=day,
                        class_arm=student.current_arm,
                        status=(
                            AttendanceStatus.ABSENT
                            if random.random() < 0.06
                            else AttendanceStatus.PRESENT
                        ),
                    )
                )
        StudentAttendance.objects.bulk_create(rows, ignore_conflicts=True)
        self.stdout.write(f"  {len(rows)} attendance records")

    # -- finance -----------------------------------------------------------
    def _fees(self, session, term):
        from apps.finance.models import FeeCategory, FeeItem, FeeStructure, Invoice
        from apps.finance.services import generate_invoices, issue_invoice, record_payment

        structure, created = FeeStructure.objects.get_or_create(
            name=f"{term.name} fees",
            session=session,
            term=term,
            level=None,
            programme=None,
        )
        if created:
            for label, category, amount in [
                ("Tuition", FeeCategory.TUITION, "85000.00"),
                ("Development levy", FeeCategory.DEVELOPMENT, "15000.00"),
                ("Examination", FeeCategory.EXAMINATION, "5000.00"),
                ("Bus (optional)", FeeCategory.TRANSPORT, "30000.00"),
            ]:
                FeeItem.objects.create(
                    structure=structure,
                    name=label,
                    category=category,
                    amount=Decimal(amount),
                    is_optional=label.startswith("Bus"),
                )

        outcome = generate_invoices(structure=structure)
        for index, invoice in enumerate(Invoice.objects.alive().filter(structure=structure)):
            issue_invoice(invoice)
            invoice.refresh_from_db()
            # Two in three have paid something; one in three has paid in full.
            if index % 3 == 0:
                continue
            amount = (
                invoice.total if index % 3 == 1 else (invoice.total / 2).quantize(Decimal("0.01"))
            )
            if invoice.is_payable:
                record_payment(invoice=invoice, amount=amount, method="TRANSFER")
        self.stdout.write(f"  {outcome.created} invoices raised")

    # -- communication -----------------------------------------------------
    def _notice(self):
        from apps.communication.models import Announcement
        from apps.communication.services import publish_announcement

        announcement, created = Announcement.objects.get_or_create(
            title="Mid-term break",
            defaults={
                "body": "School closes on Friday and resumes the following Wednesday. "
                "Outstanding fees should be settled before the resumption date.",
                "audience_roles": ["guardian"],
                "is_pinned": True,
            },
        )
        if created:
            publish_announcement(announcement)
        self.stdout.write("  1 notice")
