from django.contrib import admin

from .models import AssessmentComponent, GradeBand, GradingScale, Score, SubjectResult, TermResult


class GradeBandInline(admin.TabularInline):
    model = GradeBand
    extra = 0


@admin.register(GradingScale)
class GradingScaleAdmin(admin.ModelAdmin):
    list_display = ["name", "level", "pass_percentage", "uses_grade_points", "is_default"]
    list_filter = ["is_default", "uses_grade_points"]
    inlines = [GradeBandInline]


@admin.register(AssessmentComponent)
class AssessmentComponentAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "max_score", "level", "subject", "order", "is_active"]
    list_filter = ["is_active", "level"]
    search_fields = ["code", "name"]


@admin.register(Score)
class ScoreAdmin(admin.ModelAdmin):
    list_display = ["registration", "component", "score", "version", "entered_by"]
    list_filter = ["component"]
    raw_id_fields = ["registration"]


@admin.register(SubjectResult)
class SubjectResultAdmin(admin.ModelAdmin):
    """Derived data: readable in the admin, never editable there."""

    list_display = ["registration", "total_score", "percentage", "grade", "position", "is_complete"]
    list_filter = ["grade", "is_pass", "is_complete"]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(TermResult)
class TermResultAdmin(admin.ModelAdmin):
    list_display = ["enrolment", "term", "average", "position", "gpa", "cgpa", "promotion_status"]
    list_filter = ["term", "promotion_status"]
    readonly_fields = [
        "enrolment",
        "term",
        "subjects_count",
        "total_score",
        "max_total",
        "average",
        "credit_units_registered",
        "credit_units_earned",
        "gpa",
        "cgpa",
        "position",
        "cohort_size",
        "days_present",
        "days_total",
    ]

    def has_add_permission(self, request) -> bool:
        return False
