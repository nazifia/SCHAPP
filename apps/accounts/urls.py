from rest_framework.routers import DefaultRouter

from .views import RoleViewSet, UserViewSet

app_name = "accounts"

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")
router.register("roles", RoleViewSet, basename="role")

urlpatterns = router.urls
