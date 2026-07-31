from django.contrib import admin

from .models import Application, Guardian, Staff, Student, StudentDocument, StudentGuardian


class StudentGuardianInline(admin.TabularInline):
    model = StudentGuardian
    extra = 0


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "admission_number",
        "full_name",
        "status",
        "current_level",
        "current_arm",
        "programme",
    )
    list_filter = ("status", "current_level", "current_arm", "programme", "gender")
    search_fields = ("first_name", "last_name", "admission_number", "matriculation_number")
    inlines = [StudentGuardianInline]
    # NIN is encrypted and shown masked; it is never an editable admin field.
    exclude = ("nin",)
    readonly_fields = ("masked_nin",)


@admin.register(Guardian)
class GuardianAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "occupation")
    search_fields = ("first_name", "last_name", "phone")
    exclude = ("nin",)


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = (
        "staff_number",
        "full_name",
        "staff_type",
        "designation",
        "department",
        "status",
    )
    list_filter = ("staff_type", "department", "status")
    search_fields = ("first_name", "last_name", "staff_number")
    exclude = ("nin", "bank_account_number")
    readonly_fields = ("masked_nin",)


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("reference", "full_name", "session", "level_applied", "status", "created_at")
    list_filter = ("status", "session", "level_applied")
    search_fields = ("reference", "first_name", "last_name")
    exclude = ("nin",)


admin.site.register(StudentDocument)
