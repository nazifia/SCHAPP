from django.contrib import admin

from .models import (
    AcademicSession,
    ClassArm,
    ClassLevel,
    Department,
    Enrolment,
    Faculty,
    Programme,
    Room,
    Stream,
    Subject,
    SubjectRegistration,
    TeachingAssignment,
    Term,
    TimetableEntry,
)


class TermInline(admin.TabularInline):
    model = Term
    extra = 0


@admin.register(AcademicSession)
class AcademicSessionAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_current")
    inlines = [TermInline]


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ("name", "session", "index", "is_current", "results_published_at")
    list_filter = ("session", "is_current")


@admin.register(ClassLevel)
class ClassLevelAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "order", "next_level", "is_terminal")
    ordering = ["order"]


@admin.register(ClassArm)
class ClassArmAdmin(admin.ModelAdmin):
    list_display = ("__str__", "stream", "form_teacher", "capacity", "is_active")
    list_filter = ("level", "stream", "is_active")


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "category", "credit_units", "level", "is_active")
    list_filter = ("category", "level", "department", "is_active")
    search_fields = ("code", "title")
    filter_horizontal = ("prerequisites",)


@admin.register(Programme)
class ProgrammeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "award", "department", "is_active")
    list_filter = ("award", "department", "is_active")


@admin.register(Enrolment)
class EnrolmentAdmin(admin.ModelAdmin):
    list_display = ("student", "session", "level", "class_arm", "status")
    list_filter = ("session", "level", "class_arm", "status")
    search_fields = ("student__first_name", "student__last_name", "student__admission_number")


@admin.register(SubjectRegistration)
class SubjectRegistrationAdmin(admin.ModelAdmin):
    list_display = ("enrolment", "subject", "term", "status", "is_carryover")
    list_filter = ("term", "status", "is_carryover")


@admin.register(TeachingAssignment)
class TeachingAssignmentAdmin(admin.ModelAdmin):
    list_display = ("staff", "subject", "session", "term", "class_arm", "level")
    list_filter = ("session", "term", "subject")


@admin.register(TimetableEntry)
class TimetableEntryAdmin(admin.ModelAdmin):
    list_display = ("term", "day", "start_time", "end_time", "subject", "class_arm", "room")
    list_filter = ("term", "day", "class_arm")


admin.site.register([Stream, Faculty, Department, Room])
