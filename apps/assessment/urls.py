from rest_framework.routers import DefaultRouter

from .views import (
    AssessmentComponentViewSet,
    BroadsheetView,
    GradingScaleViewSet,
    PromotionView,
    ScoreViewSet,
    SubjectResultViewSet,
    TermResultViewSet,
    TranscriptView,
)

app_name = "assessment"

router = DefaultRouter()
router.register("components", AssessmentComponentViewSet, basename="component")
router.register("grading-scales", GradingScaleViewSet, basename="grading-scale")
router.register("scores", ScoreViewSet, basename="score")
router.register("subject-results", SubjectResultViewSet, basename="subject-result")
router.register("term-results", TermResultViewSet, basename="term-result")
router.register("broadsheet", BroadsheetView, basename="broadsheet")
router.register("transcript", TranscriptView, basename="transcript")
router.register("promotions", PromotionView, basename="promotion")

urlpatterns = router.urls
