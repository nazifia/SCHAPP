from django.urls import path

from .views import (
    DeviceListView,
    DeviceRevokeView,
    LogoutView,
    MeView,
    OtpRequestView,
    OtpVerifyView,
    PinLoginView,
    PinSetView,
    PushTokenView,
    TokenRefreshView,
)

app_name = "auth_phone"

urlpatterns = [
    path("otp/request/", OtpRequestView.as_view(), name="otp-request"),
    path("otp/verify/", OtpVerifyView.as_view(), name="otp-verify"),
    path("pin/login/", PinLoginView.as_view(), name="pin-login"),
    path("pin/set/", PinSetView.as_view(), name="pin-set"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("devices/", DeviceListView.as_view(), name="device-list"),
    path("devices/push-token/", PushTokenView.as_view(), name="device-push-token"),
    path("devices/<uuid:device_id>/", DeviceRevokeView.as_view(), name="device-revoke"),
    path("me/", MeView.as_view(), name="me"),
]
