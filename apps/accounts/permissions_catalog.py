"""Permissions are declared in code; roles only reference these codes.

A permission that no code checks is a lie, so the catalogue lives next to the
checks rather than in a database table an admin can invent rows in. Roles are
per-tenant data and hold a list of these codes.

Three codes are exceptions, marked `PLACEHOLDER` below: they name a module this
application does not have. They are not lies about a control that is missing —
they are the role shape a school already recognises, waiting for the feature.
Nothing is gated on them because there is nothing to gate. Anything else in
this file must be checked somewhere, and a sweep for the ones that were not is
how several of these checks got written.
"""

# code -> human description
PERMISSIONS: dict[str, str] = {
    # People
    # Two levels, and the difference is the whole of object-level scoping:
    # `view_student` means "may see student records", narrowed by whatever
    # class, course or ward the holder is attached to; `view_all_students`
    # means the whole institution, with no narrowing at all.
    "people.view_student": "View student records (scoped to one's own classes/wards)",
    "people.view_all_students": "View every student record in the institution",
    "people.manage_student": "Create and edit student records",
    "people.view_staff": "View staff records",
    "people.manage_staff": "Create and edit staff records",
    "people.manage_guardian": "Link and edit guardians",
    "people.view_own_ward": "View records of one's own children/wards",
    # Admissions
    "admissions.review": "Screen and decide on applications",
    # Academics
    "academics.manage_structure": "Manage sessions, terms, classes, programmes, courses",
    "academics.manage_timetable": "Build and publish timetables",
    "academics.manage_enrolment": "Enrol students and approve course registration",
    "academics.approve_registration": "Approve course registration as adviser or HOD",
    # Assessment
    "assessment.enter_score": "Enter scores for assigned classes or courses",
    "assessment.view_all_scores": "View scores across the institution",
    "assessment.publish_results": "Publish results to students and guardians",
    "assessment.generate_report": "Generate report cards, broadsheets and transcripts",
    "assessment.override_promotion": "Override the promotion engine with a reason",
    # Attendance
    "attendance.mark": "Mark attendance",
    "attendance.view_all": "View attendance across the institution",
    # Finance
    "finance.view_invoice": "View invoices",
    "finance.manage_fees": "Define fee structures and raise invoices",
    # Deliberately not `manage_fees`. Setting next term's price list and
    # changing what one family already owes are different decisions with
    # different signatures behind them: the bursar prices the school, the head
    # approves the bill a parent is holding.
    "finance.adjust_invoice": "Adjust a raised bill — its lines and its discount",
    "finance.record_payment": "Record and reconcile payments",
    # PLACEHOLDER: no payroll module. `Staff.bank_name` and
    # `Staff.bank_account_number` are the other half of the same waiting room.
    "finance.manage_payroll": "Run payroll",
    # Communication
    "communication.send_sms": "Send SMS to parents and staff",
    "communication.manage_announcement": "Publish announcements",
    # Library / hostel — PLACEHOLDER: no such modules, no phase promised them.
    "library.manage": "Manage the catalogue and loans",
    "hostel.manage": "Manage rooms and allocation",
    # Administration
    "admin.manage_users": "Create users and assign roles",
    "admin.manage_roles": "Create and edit roles",
    "admin.manage_settings": "Change institution settings and branding",
    "admin.view_audit": "Read the audit trail",
    # Self-service
    "self.view_own_results": "View one's own results",
    "self.view_own_invoice": "View one's own invoices",
}

#: Wildcard held by school owners. Checked explicitly, never stored expanded,
#: so a permission added in a later release is covered without a data migration.
ALL = "*"


def is_known(code: str) -> bool:
    return code == ALL or code in PERMISSIONS


#: code -> (display name, permissions). Seeded into every new tenant schema.
SYSTEM_ROLES: dict[str, tuple[str, list[str]]] = {
    "school_admin": ("School Admin / Proprietor", [ALL]),
    "principal": (
        "Principal / Rector / Provost",
        [
            "people.view_student",
            "people.view_all_students",
            "people.manage_student",
            "people.view_staff",
            "academics.manage_structure",
            "academics.manage_timetable",
            "assessment.view_all_scores",
            "assessment.publish_results",
            "assessment.generate_report",
            "assessment.override_promotion",
            "attendance.view_all",
            "finance.view_invoice",
            "finance.adjust_invoice",
            "communication.manage_announcement",
            "communication.send_sms",
            "admin.view_audit",
            "admissions.review",
        ],
    ),
    "vice_principal": (
        "Vice Principal / HOD",
        [
            "people.view_student",
            "people.view_all_students",
            "people.view_staff",
            "academics.manage_timetable",
            "academics.approve_registration",
            "assessment.view_all_scores",
            "assessment.generate_report",
            "attendance.view_all",
            "communication.manage_announcement",
        ],
    ),
    "registrar": (
        "Registrar / Exams Officer",
        [
            "people.view_student",
            "people.view_all_students",
            "people.manage_student",
            "academics.manage_structure",
            "academics.manage_enrolment",
            "assessment.view_all_scores",
            "assessment.publish_results",
            "assessment.generate_report",
            "admissions.review",
        ],
    ),
    "bursar": (
        "Bursar / Accountant",
        [
            "people.view_student",
            "people.view_all_students",
            "finance.view_invoice",
            "finance.manage_fees",
            "finance.adjust_invoice",
            "finance.record_payment",
            "finance.manage_payroll",
        ],
    ),
    "teacher": (
        "Teacher / Lecturer",
        [
            "people.view_student",
            "assessment.enter_score",
            "attendance.mark",
            "academics.manage_timetable",
        ],
    ),
    "course_adviser": (
        "Course Adviser",
        [
            "people.view_student",
            "academics.approve_registration",
            "assessment.view_all_scores",
        ],
    ),
    "librarian": ("Librarian", ["library.manage", "people.view_all_students"]),
    "hostel_warden": ("Hostel Warden", ["hostel.manage", "people.view_all_students"]),
    "counsellor": ("Counsellor", ["people.view_all_students", "attendance.view_all"]),
    "student": ("Student", ["self.view_own_results", "self.view_own_invoice"]),
    "guardian": (
        "Parent / Guardian",
        ["people.view_own_ward", "self.view_own_results", "self.view_own_invoice"],
    ),
    "alumni": ("Alumni", ["self.view_own_results"]),
}
