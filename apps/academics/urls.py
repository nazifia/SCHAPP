from rest_framework.routers import DefaultRouter

from .views import (
    AcademicSessionViewSet,
    ClassArmViewSet,
    ClassLevelViewSet,
    DepartmentViewSet,
    EnrolmentViewSet,
    FacultyViewSet,
    MyClassesView,
    ProgrammeViewSet,
    RoomViewSet,
    StreamViewSet,
    SubjectRegistrationViewSet,
    SubjectViewSet,
    TeachingAssignmentViewSet,
    TermViewSet,
    TimetableEntryViewSet,
)

app_name = "academics"

router = DefaultRouter()
router.register("sessions", AcademicSessionViewSet, basename="session")
router.register("terms", TermViewSet, basename="term")
router.register("levels", ClassLevelViewSet, basename="level")
router.register("streams", StreamViewSet, basename="stream")
router.register("faculties", FacultyViewSet, basename="faculty")
router.register("departments", DepartmentViewSet, basename="department")
router.register("programmes", ProgrammeViewSet, basename="programme")
router.register("arms", ClassArmViewSet, basename="arm")
router.register("subjects", SubjectViewSet, basename="subject")
router.register("rooms", RoomViewSet, basename="room")
router.register("teaching-assignments", TeachingAssignmentViewSet, basename="teaching-assignment")
router.register("enrolments", EnrolmentViewSet, basename="enrolment")
router.register("registrations", SubjectRegistrationViewSet, basename="registration")
router.register("timetable", TimetableEntryViewSet, basename="timetable")
router.register("my-classes", MyClassesView, basename="my-classes")

urlpatterns = router.urls
