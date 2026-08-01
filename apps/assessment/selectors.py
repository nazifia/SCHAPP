"""Reads: scale/component resolution, mark sheets, broadsheets, transcripts.

The resolution rules are the interesting part. Everything else here shapes
already-computed rows into the three documents a school prints.
"""

from django.db.models import QuerySet

from apps.academics.models import Enrolment, RegistrationStatus, SubjectRegistration

from .models import AssessmentComponent, GradingScale, SubjectResult, TermResult

#: Registrations that count towards a result. A dropped or rejected course
#: does not appear on a report card.
COUNTED_STATUSES = [
    RegistrationStatus.APPROVED,
    RegistrationStatus.ADVISER_APPROVED,
    RegistrationStatus.SUBMITTED,
]


def components_for(subject=None, level=None) -> list[AssessmentComponent]:
    """The mark-sheet columns for one subject at one level.

    Most specific wins and does not merge: a subject that declares its own
    components declares *all* of them, so a practical subject can be 40/60
    without inheriting a stray exam column from the school default.
    """
    active = AssessmentComponent.objects.filter(is_active=True)

    if subject is not None:
        specific = list(active.filter(subject=subject))
        if specific:
            return specific
    if level is not None:
        by_level = list(active.filter(level=level, subject__isnull=True))
        if by_level:
            return by_level
    return list(active.filter(level__isnull=True, subject__isnull=True))


def scale_for(level=None) -> GradingScale | None:
    """The grading scale for a level, falling back to the school default."""
    if level is not None:
        scoped = GradingScale.objects.filter(level=level).first()
        if scoped is not None:
            return scoped
    return GradingScale.objects.filter(is_default=True).first() or GradingScale.objects.first()


def cohort_key(enrolment: Enrolment) -> tuple:
    """Who a student is ranked against.

    Secondary: their arm. Tertiary: their level and programme — a 200-level
    Computer Science student is not in competition with 200-level Accountancy.
    """
    if enrolment.class_arm_id:
        return ("arm", enrolment.class_arm_id)
    return ("level", enrolment.level_id, enrolment.programme_id)


def counted_registrations(term, **filters) -> QuerySet[SubjectRegistration]:
    return (
        SubjectRegistration.objects.filter(term=term, status__in=COUNTED_STATUSES, **filters)
        .select_related(
            "subject",
            "enrolment",
            "enrolment__student",
            "enrolment__level",
            "enrolment__class_arm",
        )
        .order_by("subject__code")
    )


def mark_sheet(*, term, subject, class_arm=None, level=None, students=None) -> dict:
    """Everything a teacher's entry screen needs, in one payload.

    Returns the component columns and one row per registered student with the
    marks already stored, so the app can be filled in offline and synced back
    through `POST /assessment/scores/bulk/`.
    """
    filters: dict = {"subject": subject}
    if class_arm is not None:
        filters["enrolment__class_arm"] = class_arm
    elif level is not None:
        filters["enrolment__level"] = level

    registrations = counted_registrations(term, **filters).prefetch_related("scores")
    if students is not None:
        registrations = registrations.filter(enrolment__student__in=students)

    components = components_for(subject=subject, level=level or getattr(class_arm, "level", None))
    rows = []
    for registration in registrations.order_by(
        "enrolment__student__last_name", "enrolment__student__first_name"
    ):
        marks = {
            str(s.component_id): {"score": s.score, "version": s.version}
            for s in registration.scores.all()
        }
        student = registration.enrolment.student
        rows.append(
            {
                "student": student.pk,
                "full_name": student.full_name,
                "admission_number": student.display_number,
                "registration": registration.pk,
                "is_carryover": registration.is_carryover,
                "scores": marks,
            }
        )
    return {
        "term": term.pk,
        "subject": subject.pk,
        "components": [
            {
                "id": c.pk,
                "code": c.code,
                "name": c.name,
                "max_score": c.max_score,
                "order": c.order,
            }
            for c in components
        ],
        "rows": rows,
    }


def broadsheet(*, term, class_arm=None, level=None) -> dict:
    """The whole cohort as a grid: students down, subjects across.

    One query for the subject results and one for the term results; the grid
    is assembled in Python because a pivot in SQL would still need a second
    pass for the ordering the school expects.
    """
    filters: dict = {}
    if class_arm is not None:
        filters["registration__enrolment__class_arm"] = class_arm
    elif level is not None:
        filters["registration__enrolment__level"] = level

    results = (
        SubjectResult.objects.filter(registration__term=term, **filters)
        .select_related(
            "registration__subject",
            "registration__enrolment__student",
            "registration__enrolment__programme__department",
        )
        .order_by(
            "registration__enrolment__student__last_name",
            "registration__subject__code",
        )
    )

    subjects: dict = {}
    students: dict = {}
    for result in results:
        subject = result.registration.subject
        subjects.setdefault(
            subject.pk, {"id": subject.pk, "code": subject.code, "title": subject.title}
        )
        enrolment = result.registration.enrolment
        student = enrolment.student
        row = students.setdefault(
            student.pk,
            {
                "student": student.pk,
                "full_name": student.full_name,
                "admission_number": student.display_number,
                # Tertiary: which course of study, and whose department owns it.
                # Null throughout for a secondary school, which has neither.
                "programme": str(enrolment.programme) if enrolment.programme_id else None,
                "department": (
                    str(enrolment.programme.department) if enrolment.programme_id else None
                ),
                "subjects": {},
            },
        )
        row["subjects"][str(subject.pk)] = {
            "total": result.total_score,
            "percentage": result.percentage,
            "grade": result.grade,
            "position": result.position,
        }

    term_results = {
        str(tr.enrolment.student_id): tr
        for tr in TermResult.objects.filter(term=term).select_related("enrolment")
    }
    columns = list(subjects.values())
    for student_id, row in students.items():
        summary = term_results.get(str(student_id))
        row["average"] = summary.average if summary else None
        row["position"] = summary.position if summary else None
        row["gpa"] = summary.gpa if summary else None
        row["cgpa"] = summary.cgpa if summary else None
        # Column-aligned copy: a template cannot look a subject up by id, and a
        # client rendering a grid would have to do this itself anyway.
        row["cells"] = [row["subjects"].get(str(column["id"])) for column in columns]

    rows = sorted(students.values(), key=lambda r: r["full_name"])
    # One programme for the whole cohort belongs in the heading; a level-wide
    # tertiary sheet mixes them, and then it belongs in a column, because "whose
    # student is this?" is the first question asked of any row on it.
    departments = {row["department"] for row in rows}
    programmes = {row["programme"] for row in rows}
    one_department = departments.pop() if len(departments) == 1 else None
    one_programme = programmes.pop() if len(programmes) == 1 else None
    return {
        "term": str(term),
        # Whose sheet this is. A printed sheet with no class on it is one a head
        # of department cannot file, and "all classes" is a real answer.
        "cohort": str(class_arm or level or "All classes"),
        "department": one_department,
        "programme": one_programme,
        "mixed_programmes": one_programme is None and any(row["programme"] for row in rows),
        "subjects": columns,
        "rows": rows,
        # A GPA anywhere in the cohort means a tertiary scale, and the sheet
        # closes with GPA/CGPA instead of average and position.
        "uses_grade_points": any(row["gpa"] is not None for row in rows),
    }


def report_card_context(term_result: TermResult) -> dict:
    """The report card for one student, ready for a template or a serializer."""
    from .grading import ordinal

    enrolment = term_result.enrolment
    results = (
        SubjectResult.objects.filter(
            registration__term=term_result.term, registration__enrolment=enrolment
        )
        .select_related("registration__subject")
        .prefetch_related("registration__scores__component")
    )

    subjects = []
    for result in results:
        registration = result.registration
        subjects.append(
            {
                "code": registration.subject.code,
                "title": registration.subject.title,
                "credit_units": result.credit_units,
                "components": [
                    {"code": s.component.code, "score": s.score, "max_score": s.component.max_score}
                    for s in sorted(registration.scores.all(), key=lambda s: s.component.order)
                ],
                "total": result.total_score,
                "max_total": result.max_total,
                "percentage": result.percentage,
                "grade": result.grade,
                "grade_point": result.grade_point,
                "position": ordinal(result.position or 0),
                "class_average": result.class_average,
                "class_highest": result.class_highest,
                "remark": result.teacher_remark,
                "is_carryover": registration.is_carryover,
            }
        )

    return {
        "student": enrolment.student,
        "enrolment": enrolment,
        # The programme is on the enrolment; the department owning it is one hop
        # further and a tertiary slip is filed under it.
        "department": (
            str(enrolment.programme.department) if enrolment.programme_id else None
        ),
        "term": term_result.term,
        "session": term_result.term.session,
        "result": term_result,
        "position": ordinal(term_result.position or 0),
        "subjects": subjects,
    }


def transcript_context(student) -> dict:
    """Every term the student has a result for, oldest first.

    Tertiary institutions print this as the academic transcript; a secondary
    school gets the same thing as a cumulative record. Withdrawn or dropped
    courses never appear, because they never produced a `SubjectResult`.
    """
    term_results = (
        TermResult.objects.filter(enrolment__student=student)
        .select_related(
            "term",
            "term__session",
            "enrolment__level",
            "enrolment__programme__department",
        )
        .order_by("term__session__start_date", "term__index")
    )

    terms = []
    for term_result in term_results:
        results = (
            SubjectResult.objects.filter(
                registration__term=term_result.term, registration__enrolment=term_result.enrolment
            )
            .select_related("registration__subject")
            .order_by("registration__subject__code")
        )
        terms.append(
            {
                "session": term_result.term.session.name,
                "term": term_result.term.name,
                # Names, not the model instances: the same dict is both the PDF
                # context and the JSON body, and a `ClassLevel` is not
                # serialisable. The template printed `str()` of them anyway.
                "level": str(term_result.enrolment.level) if term_result.enrolment.level else None,
                "programme": (
                    str(term_result.enrolment.programme)
                    if term_result.enrolment.programme
                    else None
                ),
                "department": (
                    str(term_result.enrolment.programme.department)
                    if term_result.enrolment.programme
                    else None
                ),
                "subjects": [
                    {
                        "code": r.registration.subject.code,
                        "title": r.registration.subject.title,
                        "credit_units": r.credit_units,
                        "percentage": r.percentage,
                        "grade": r.grade,
                        "grade_point": r.grade_point,
                    }
                    for r in results
                ],
                "average": term_result.average,
                "gpa": term_result.gpa,
                "cgpa": term_result.cgpa,
                "credit_units_earned": term_result.credit_units_earned,
            }
        )

    latest = term_results.last()
    # Where the student stands now: the last enrolment's programme, or their own
    # when nothing has been graded yet. It cannot hang off `student` — the JSON
    # body replaces that object with a flat three-key dict in the view.
    programme = (latest.enrolment.programme if latest else None) or student.programme
    return {
        "student": student,
        "programme": str(programme) if programme else None,
        "department": str(programme.department) if programme else None,
        "terms": terms,
        "cgpa": latest.cgpa if latest else None,
        "total_credit_units": sum(t["credit_units_earned"] for t in terms),
    }


#: Reading a mark is not a by-product of being able to look a student up. A
#: bursar, a librarian and a hostel warden all hold `people.view_all_students`
#: and none of them has any business knowing what a child scored. One of these
#: three codes is the grant; scoping then narrows it to the right rows.
RESULT_READERS = (
    "assessment.view_all_scores",  # exams officer, principal, adviser
    "assessment.enter_score",  # a teacher must read the marks they enter
    "self.view_own_results",  # student, guardian, alumnus
)


def may_read_results(user) -> bool:
    return bool(user) and any(user.has_perm_code(code) for code in RESULT_READERS)


def results_visible_to(user) -> QuerySet[TermResult]:
    """Published results only, unless the caller may see everything.

    A parent must not read a term result the exams officer is still checking,
    so publication — not merely computation — is what makes a row visible.
    """
    from apps.people.selectors import students_visible_to

    if not may_read_results(user):
        return TermResult.objects.none()

    queryset = TermResult.objects.filter(
        enrolment__student__in=students_visible_to(user)
    ).select_related("enrolment__student", "enrolment__programme__department", "term")

    if user.has_perm_code("assessment.view_all_scores"):
        return queryset
    return queryset.filter(term__results_published_at__isnull=False)
