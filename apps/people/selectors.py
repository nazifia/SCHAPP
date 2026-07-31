"""Read paths, including the object-level scoping rules.

Schema isolation keeps one school out of another's data. These functions do
the second job: keeping a teacher out of a class they do not teach, and a
guardian out of a child that is not theirs.

Scoping belongs in the queryset, not in a per-object permission check — a
list endpoint that filters after the fact still leaks row counts and
pagination cursors.
"""

from django.db.models import Q, QuerySet

from .models import Staff, Student


def staff_for(user) -> Staff | None:
    return Staff.objects.filter(user=user).select_related("department").first()


def guardian_for(user):
    from .models import Guardian

    return Guardian.objects.filter(user=user).first()


def students_visible_to(user) -> QuerySet[Student]:
    """The students this user may see at all.

    * anyone with `people.view_all_students` (admin, principal, registrar,
      bursar, librarian, warden, counsellor): everyone;
    * a teacher with `people.view_student`: only students in classes or
      courses they are assigned to;
    * a guardian with `people.view_own_ward`: only their wards;
    * a student: only themselves;
    * anyone else: nothing.
    """
    base = Student.objects.alive()

    if not (user and user.is_authenticated):
        return base.none()
    if user.has_perm_code("people.view_all_students"):
        return base

    conditions = Q(pk__in=[])

    staff = staff_for(user)
    # A teaching assignment says who stands in front of the class; it is
    # `people.view_student` that says they may read those records. Without this
    # the permission is decorative and a timetable entry is the whole grant.
    if staff is not None and user.has_perm_code("people.view_student"):
        assignments = staff.teaching_assignments.all()
        arm_ids = [a.class_arm_id for a in assignments if a.class_arm_id]
        level_ids = [a.level_id for a in assignments if a.level_id]
        # Form teachers keep sight of their whole arm even out of lesson time.
        arm_ids += list(staff.form_classes.values_list("id", flat=True))
        if arm_ids:
            conditions |= Q(current_arm_id__in=arm_ids)
        if level_ids:
            conditions |= Q(current_level_id__in=level_ids)

    if user.has_perm_code("people.view_own_ward"):
        guardian = guardian_for(user)
        if guardian is not None:
            conditions |= Q(guardian_links__guardian=guardian)

    conditions |= Q(user=user)  # a student may always see themselves

    return base.filter(conditions).distinct()


def can_view_student(user, student: Student) -> bool:
    return students_visible_to(user).filter(pk=student.pk).exists()


def wards_of(user) -> QuerySet[Student]:
    guardian = guardian_for(user)
    if guardian is None:
        return Student.objects.none()
    return Student.objects.alive().filter(guardian_links__guardian=guardian).distinct()
