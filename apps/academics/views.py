from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import RequirePermission
from apps.api.deletion import ProtectDependentsMixin
from apps.api.mixins import TenantScopedViewSet, lookup_param
from apps.people.selectors import staff_for
from apps.tenants.db import tenant_atomic

from . import selectors, services
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
from .serializers import (
    AcademicSessionSerializer,
    AllocateToArmSerializer,
    ClassArmSerializer,
    ClassLevelSerializer,
    DepartmentSerializer,
    EnrolmentSerializer,
    FacultySerializer,
    ProgrammeSerializer,
    RegisterSubjectsSerializer,
    RejectRegistrationSerializer,
    RoomSerializer,
    StreamSerializer,
    SubjectRegistrationSerializer,
    SubjectSerializer,
    TeachingAssignmentSerializer,
    TermSerializer,
    TimetableEntrySerializer,
)

ManageStructure = RequirePermission("academics.manage_structure")
ManageTimetable = RequirePermission("academics.manage_timetable")
ManageEnrolment = RequirePermission("academics.manage_enrolment")


class StructureViewSet(ProtectDependentsMixin, TenantScopedViewSet, viewsets.ModelViewSet):
    """Reference data: everyone signed in may read it, few may change it.

    A timetable or a class list is useless to a teacher who cannot resolve
    subject and level names, so reads are open to any authenticated user
    inside the school.

    Deleting one of these rows used to cascade an academic year into nothing —
    see `apps.api.deletion`. Most of them carry an `is_active` flag, which is
    what "we no longer offer this" should have meant all along.
    """

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [ManageStructure()]


class AcademicSessionViewSet(StructureViewSet):
    queryset = AcademicSession.objects.all()
    serializer_class = AcademicSessionSerializer
    filterset_fields = ["is_current"]

    @extend_schema(responses={200: AcademicSessionSerializer})
    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def current(self, request):
        session = selectors.current_session()
        if session is None:
            return Response(
                {
                    "error": {
                        "code": "NO_CURRENT_SESSION",
                        "message": "No session has been marked current.",
                        "details": {},
                    }
                },
                status=404,
            )
        return Response(self.get_serializer(session).data)

    @extend_schema(
        request=None,
        responses={200: AcademicSessionSerializer},
        description="Roll the school over to this session. Picks the term by date.",
    )
    @action(detail=True, methods=["post"], url_path="set-current")
    def set_current(self, request, pk=None):
        session = services.set_current_session(session=self.get_object(), actor=request.user)
        return Response(self.get_serializer(session).data)


class TermViewSet(StructureViewSet):
    queryset = Term.objects.select_related("session")
    serializer_class = TermSerializer
    filterset_fields = ["session", "is_current", "index"]

    @extend_schema(responses={200: TermSerializer})
    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def current(self, request):
        term = selectors.current_term()
        if term is None:
            return Response(
                {
                    "error": {
                        "code": "NO_CURRENT_TERM",
                        "message": "No term has been marked current.",
                        "details": {},
                    }
                },
                status=404,
            )
        return Response(self.get_serializer(term).data)

    @extend_schema(
        request=None,
        responses={200: TermSerializer},
        description="Advance to this term. Its session becomes current too.",
    )
    @action(detail=True, methods=["post"], url_path="set-current")
    def set_current(self, request, pk=None):
        term = services.set_current_term(term=self.get_object(), actor=request.user)
        return Response(self.get_serializer(term).data)


class ClassLevelViewSet(StructureViewSet):
    queryset = ClassLevel.objects.all()
    serializer_class = ClassLevelSerializer


class StreamViewSet(StructureViewSet):
    queryset = Stream.objects.all()
    serializer_class = StreamSerializer


class FacultyViewSet(StructureViewSet):
    queryset = Faculty.objects.all()
    serializer_class = FacultySerializer


class DepartmentViewSet(StructureViewSet):
    queryset = Department.objects.select_related("faculty")
    serializer_class = DepartmentSerializer
    filterset_fields = ["faculty"]


class ProgrammeViewSet(StructureViewSet):
    queryset = Programme.objects.select_related("department")
    serializer_class = ProgrammeSerializer
    filterset_fields = ["department", "award", "is_active"]


class ClassArmViewSet(StructureViewSet):
    queryset = ClassArm.objects.select_related("level", "stream", "form_teacher")
    serializer_class = ClassArmSerializer
    filterset_fields = ["level", "stream", "is_active"]

    def get_queryset(self):
        return selectors.with_occupancy(super().get_queryset(), selectors.current_session())

    @extend_schema(
        request=AllocateToArmSerializer,
        responses={200: EnrolmentSerializer(many=True)},
        description="Place a batch of students in this class. All or nothing.",
    )
    @action(detail=True, methods=["post"], permission_classes=[ManageEnrolment])
    def allocate(self, request, pk=None):
        arm = self.get_object()
        serializer = AllocateToArmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        enrolments = services.allocate_to_arm(
            enrolments=list(serializer.validated_data["enrolments"]),
            class_arm=arm,
            enforce_capacity=serializer.validated_data["enforce_capacity"],
            actor=request.user,
        )
        return Response(EnrolmentSerializer(enrolments, many=True).data)

    @extend_schema(responses={200: EnrolmentSerializer(many=True)})
    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated])
    def students(self, request, pk=None):
        """The class list. Scoped: a teacher only sees arms they are on."""
        arm = self.get_object()
        session = selectors.current_session()
        if session is None:
            return Response([])

        staff = staff_for(request.user)
        scoped = not request.user.has_perm_code("people.view_all_students")
        if scoped and (
            staff is None or not selectors.arms_taught_by(staff).filter(pk=arm.pk).exists()
        ):
            return Response(
                {
                    "error": {
                        "code": "NOT_YOUR_CLASS",
                        "message": "You are not assigned to this class.",
                        "details": {},
                    }
                },
                status=403,
            )

        enrolments = selectors.class_list(session=session, class_arm=arm)
        return Response(EnrolmentSerializer(enrolments, many=True).data)


class SubjectViewSet(StructureViewSet):
    queryset = Subject.objects.select_related("department", "level", "stream")
    serializer_class = SubjectSerializer
    filterset_fields = ["level", "department", "stream", "category", "is_active"]
    search_fields = ["code", "title"]

    @extend_schema(responses={200: SubjectSerializer(many=True)})
    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def mine(self, request):
        """Subjects the signed-in member of staff actually teaches."""
        staff = staff_for(request.user)
        subjects = selectors.subjects_taught_by(staff, term=selectors.current_term())
        return Response(self.get_serializer(subjects, many=True).data)


class RoomViewSet(StructureViewSet):
    queryset = Room.objects.all()
    serializer_class = RoomSerializer


class TeachingAssignmentViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    queryset = TeachingAssignment.objects.select_related("staff", "subject", "class_arm", "level")
    serializer_class = TeachingAssignmentSerializer
    filterset_fields = ["staff", "subject", "session", "term", "class_arm", "level"]

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [ManageStructure()]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user.has_perm_code("people.view_staff"):
            return queryset
        # A teacher may see their own assignments and nobody else's.
        return queryset.filter(staff__user=user)


class EnrolmentViewSet(ProtectDependentsMixin, TenantScopedViewSet, viewsets.ModelViewSet):
    """One student's membership of a cohort.

    Guarded like the structure viewsets: an enrolment cascades to every
    subject registration under it, and each of those to its scores and subject
    result. Deleting a mis-keyed enrolment is routine; deleting one a term has
    been marked against is a term's marks gone.
    """

    queryset = Enrolment.objects.select_related("student", "level", "class_arm", "programme")
    serializer_class = EnrolmentSerializer
    filterset_fields = ["session", "level", "class_arm", "programme", "status"]
    permission_classes = [ManageEnrolment]

    def get_queryset(self):
        from apps.people.selectors import students_visible_to

        return super().get_queryset().filter(student__in=students_visible_to(self.request.user))

    def perform_create(self, serializer):
        data = serializer.validated_data
        enrolment = services.enrol_student(
            student=data["student"],
            session=data["session"],
            level=data["level"],
            class_arm=data.get("class_arm"),
            programme=data.get("programme"),
            roll_number=data.get("roll_number"),
        )
        serializer.instance = enrolment

    def perform_update(self, serializer):
        """A change of class goes through `assign_to_arm`, never through the field.

        `perform_create` was routed to the service and this was not, so a PATCH
        moved a pupil into any class at all: past capacity, into another
        level's arm, and without touching `Student.current_arm` — the column
        attendance, the student timetable and every export actually read.
        """
        arm = serializer.validated_data.pop("class_arm", None)
        # One transaction, because the arm is checked against the level the
        # same request may be changing: the save has to land first, and a
        # refused arm must take the level with it on the way out.
        with tenant_atomic():
            enrolment = serializer.save()
            if arm is not None and arm.pk != enrolment.class_arm_id:
                services.assign_to_arm(enrolment=enrolment, class_arm=arm, actor=self.request.user)

    @extend_schema(responses={200: EnrolmentSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def unplaced(self, request):
        """Active students with no class this session — the allocation worklist.

        Filtered through `get_queryset` rather than off the selector, so it
        obeys the same student visibility rule as every other read here.
        """
        session = selectors.current_session()
        if session is None:
            return Response([])
        level = lookup_param(request, "level", ClassLevel)
        visible = self.get_queryset().values("pk")
        enrolments = selectors.unplaced_enrolments(session=session, level=level).filter(
            pk__in=visible
        )
        return Response(self.get_serializer(enrolments, many=True).data)


class SubjectRegistrationViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    queryset = SubjectRegistration.objects.select_related("subject", "term", "enrolment__student")
    serializer_class = SubjectRegistrationSerializer
    filterset_fields = ["term", "status", "subject", "is_carryover"]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        from apps.people.selectors import students_visible_to

        return (
            super()
            .get_queryset()
            .filter(enrolment__student__in=students_visible_to(self.request.user))
        )

    @extend_schema(
        request=RegisterSubjectsSerializer,
        responses={201: SubjectRegistrationSerializer(many=True)},
    )
    @action(detail=False, methods=["post"])
    def register(self, request):
        serializer = RegisterSubjectsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        registrations = services.register_subjects(
            enrolment=data["enrolment"],
            term=data["term"],
            subjects=list(data["subjects"]),
            submit=data["submit"],
        )
        return Response(SubjectRegistrationSerializer(registrations, many=True).data, status=201)

    @extend_schema(responses={200: SubjectRegistrationSerializer})
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[RequirePermission("academics.approve_registration")],
    )
    def approve(self, request, pk=None):
        registration = self.get_object()
        staff = staff_for(request.user)
        services.approve_registration(
            registration=registration,
            staff=staff,
            as_hod=bool(request.data.get("as_hod")),
            ignore_minimum=bool(request.data.get("ignore_minimum")),
        )
        return Response(self.get_serializer(registration).data)

    @extend_schema(
        request=RejectRegistrationSerializer,
        responses={200: SubjectRegistrationSerializer},
        description="Refuse a course, with a reason the student can read.",
    )
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[RequirePermission("academics.approve_registration")],
    )
    def reject(self, request, pk=None):
        registration = self.get_object()
        serializer = RejectRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.reject_registration(
            registration=registration,
            staff=staff_for(request.user),
            reason=serializer.validated_data["reason"],
        )
        return Response(self.get_serializer(registration).data)

    @extend_schema(
        responses={200: SubjectRegistrationSerializer},
        description="Undo a refusal: the course goes back to the adviser's queue.",
    )
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[RequirePermission("academics.approve_registration")],
    )
    def reopen(self, request, pk=None):
        registration = self.get_object()
        services.reopen_registration(registration=registration, actor=request.user)
        return Response(self.get_serializer(registration).data)

    @extend_schema(responses={200: SubjectRegistrationSerializer})
    @action(detail=True, methods=["post"])
    def drop(self, request, pk=None):
        registration = self.get_object()
        services.drop_subject(registration=registration, actor=request.user)
        return Response(self.get_serializer(registration).data)


class TimetableEntryViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    queryset = TimetableEntry.objects.select_related(
        "subject", "staff", "class_arm", "level", "room"
    )
    serializer_class = TimetableEntrySerializer
    filterset_fields = ["term", "day", "class_arm", "level", "staff", "room"]

    def get_permissions(self):
        if self.action in {"list", "retrieve", "mine"}:
            return [IsAuthenticated()]
        return [ManageTimetable()]

    def perform_create(self, serializer):
        serializer.instance = services.create_timetable_entry(**serializer.validated_data)

    def perform_update(self, serializer):
        entry = TimetableEntry(pk=serializer.instance.pk, **serializer.validated_data)
        services.validate_entry(entry)
        serializer.save()

    @extend_schema(responses={200: TimetableEntrySerializer(many=True)})
    @action(detail=False, methods=["get"])
    def mine(self, request):
        """A teacher's own week, or a student's, without them guessing filters."""
        term = selectors.current_term()
        if term is None:
            return Response([])

        staff = staff_for(request.user)
        if staff is not None:
            entries = selectors.timetable_for_staff(term=term, staff=staff)
        else:
            student = getattr(request.user, "student_profile", None)
            if student is None or student.current_arm_id is None:
                return Response([])
            entries = selectors.timetable_for_arm(term=term, class_arm=student.current_arm)
        return Response(self.get_serializer(entries, many=True).data)

    @extend_schema(responses={200: TimetableEntrySerializer(many=True)})
    @action(detail=False, methods=["post"], permission_classes=[ManageTimetable])
    def check_clash(self, request):
        """Dry run: what would this period collide with?"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = TimetableEntry(**serializer.validated_data)
        clashes = services.find_clashes(entry)
        return Response(
            {"clash": bool(clashes), "entries": self.get_serializer(clashes, many=True).data}
        )


#: Codes that mean "acts on the whole school, not on a timetable". The office
#: account keying a mark a teacher never sent holds one of these and no
#: teaching assignment — `ScoreViewSet.sheet` waves it through on exactly this
#: check, so scoping the pick lists to assignments left it choosing between
#: nothing at all.
WHOLE_SCHOOL = ("assessment.view_all_scores", "attendance.view_all")


class MyClassesView(viewsets.ViewSet):
    """`/academics/my-classes/` — the teacher's home screen in one request."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ClassArmSerializer(many=True)})
    def list(self, request):
        staff = staff_for(request.user)
        term = selectors.current_term()
        if any(request.user.has_perm_code(code) for code in WHOLE_SCHOOL):
            arms = ClassArm.objects.select_related("level", "stream")
            subjects = Subject.objects.all()
        else:
            arms = selectors.arms_taught_by(staff)
            subjects = selectors.subjects_taught_by(staff, term=term)
        arms = selectors.with_occupancy(arms, term.session if term else None)
        return Response(
            {
                "term": TermSerializer(term).data if term else None,
                "arms": ClassArmSerializer(arms, many=True).data,
                "subjects": SubjectSerializer(subjects, many=True).data,
            }
        )
