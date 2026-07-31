from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from apps.accounts.platform import platform_context, platform_superuser
from apps.accounts.serializers import DeviceSerializer, UserSerializer
from apps.accounts.services import register_device
from apps.audit.models import AuditAction
from apps.audit.services import client_ip, record

from . import ratelimit, tokens
from .serializers import (
    OtpRequestResponseSerializer,
    OtpRequestSerializer,
    OtpVerifySerializer,
    PinLoginSerializer,
    PinSetSerializer,
    PushTokenSerializer,
    RefreshSerializer,
)
from .services import OtpError, request_otp, verify_otp


def _error(exc: OtpError, http_status: int = status.HTTP_400_BAD_REQUEST) -> Response:
    body = {"error": {"code": exc.code, "message": exc.message, "details": {}}}
    if exc.retry_after:
        response = Response(body, status=status.HTTP_429_TOO_MANY_REQUESTS)
        response["Retry-After"] = str(exc.retry_after)
        body["error"]["details"] = {"retry_after": exc.retry_after}
        return response
    return Response(body, status=http_status)


def _access_lifetime() -> int:
    return int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds())


def _rejection_summary(exc: TokenError, claimed: dict, request) -> str:
    """Why a refresh was refused, in the words the audit reader needs.

    A token minted for one school and presented to another fails the *family*
    lookup first — the family row lives in the other school's database — so the
    message alone reads "Session has been revoked." and hides the real cause.
    Naming both schools is the whole point of this line.
    """
    summary = f"Refresh rejected: {exc}"
    claimed_slug = claimed.get("tenant_slug") or ""
    current_slug = getattr(getattr(request, "tenant", None), "slug", "") or ""
    if claimed_slug and current_slug and claimed_slug != current_slug:
        summary += f" — token was minted for {claimed_slug}, presented to {current_slug}"
    return summary


class OtpRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "otp_request"

    @extend_schema(
        request=OtpRequestSerializer,
        responses={200: OtpRequestResponseSerializer},
        description="Always returns 200 with an opaque request_id, whether or not the number "
        "has an account. Do not infer account existence from this response.",
    )
    def post(self, request):
        serializer = OtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            issued = request_otp(
                serializer.validated_data["phone"],
                purpose=serializer.validated_data["purpose"],
                ip=client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                tenant=getattr(request, "tenant", None),
                request=request,
            )
        except OtpError as exc:
            return _error(exc)

        return Response(
            {
                "request_id": issued.request_id,
                "expires_in": issued.expires_in,
                "resend_after": issued.resend_after,
                "masked_phone": issued.masked_phone,
            }
        )


class OtpVerifyView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "otp_verify"

    @extend_schema(request=OtpVerifySerializer, responses={200: None})
    def post(self, request):
        serializer = OtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            verified = verify_otp(
                str(data["request_id"]),
                data["code"],
                request=request,
                tenant=getattr(request, "tenant", None),
            )
        except OtpError as exc:
            record(
                AuditAction.LOGIN_FAILED,
                request=request,
                summary=f"OTP verify failed: {exc.code}",
                succeeded=False,
            )
            return _error(exc, http_status=status.HTTP_401_UNAUTHORIZED)

        user = verified.user
        device_info = data.get("device") or {}

        # Everything a session owns is written where the user's row is: the
        # school's database for its own staff, the platform's for a superuser
        # signing in to a school they have no row in.
        with platform_context(user):
            device, is_new_device = register_device(user, ip=client_ip(request), **device_info)

            user.last_login = timezone.now()
            user.last_login_ip = client_ip(request)
            user.save(update_fields=["last_login", "last_login_ip", "updated_at"])

            pair = tokens.issue_for_user(
                user, tenant=getattr(request, "tenant", None), device=device
            )
            record(
                AuditAction.LOGIN_OTP,
                request=request,
                actor=user,
                summary="Signed in with OTP",
            )
            if is_new_device and device is not None:
                record(
                    AuditAction.DEVICE_REGISTERED,
                    request=request,
                    actor=user,
                    obj=device,
                    summary=f"New device: {device.name or device.platform or 'unknown'}",
                )

        return Response(
            {
                "access": pair["access"],
                "refresh": pair["refresh"],
                "expires_in": _access_lifetime(),
                "new_device": is_new_device,
                "user": UserSerializer(user).data,
            }
        )


class PinLoginView(APIView):
    """Convenience factor for staff, only after a device is already known.

    A PIN alone on an unrecognised device would reduce a two-factor login to
    six digits, so an unknown device is sent back through OTP.
    """

    permission_classes = [AllowAny]
    throttle_scope = "otp_verify"

    @extend_schema(request=PinLoginSerializer, responses={200: None})
    def post(self, request):
        serializer = PinLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        phone = data["phone"]
        device_info = data.get("device") or {}
        device_id = device_info.get("device_id", "")

        locked = ratelimit.is_locked_out(phone)
        if locked:
            return _error(OtpError("PIN_LOCKED_OUT", "Too many attempts.", locked))
        if not ratelimit.hit("pin_attempts", phone):
            seconds = ratelimit.lock_out(phone)
            return _error(OtpError("PIN_LOCKED_OUT", "Too many attempts.", seconds))

        from apps.accounts.models import User

        user = User.objects.filter(phone=phone, is_active=True).first()
        if user is None:
            # A platform superuser signing in to a school they have no row in.
            # Checked second, so a school's own staff always answer first.
            user = platform_superuser(phone=phone)
        device = (
            # Through the user: a platform superuser's devices are registered
            # in the platform database, not in whichever school they are in.
            user.devices.filter(device_id=device_id, revoked_at__isnull=True).first()
            if (user and device_id)
            else None
        )

        # One generic failure for every reason: unknown number, no PIN set,
        # wrong PIN, unknown device.
        if user is None or device is None or not user.check_pin(data["pin"]):
            record(
                AuditAction.LOGIN_FAILED,
                request=request,
                actor=user,
                summary="PIN login failed",
                succeeded=False,
            )
            return Response(
                {
                    "error": {
                        "code": "PIN_INVALID",
                        "message": "Sign in with a code sent to your phone instead.",
                        "details": {},
                    }
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        ratelimit.reset("pin_attempts", phone)
        with platform_context(user):
            register_device(user, ip=client_ip(request), **device_info)
            pair = tokens.issue_for_user(
                user, tenant=getattr(request, "tenant", None), device=device
            )
            record(AuditAction.LOGIN_PIN, request=request, actor=user, summary="Signed in with PIN")
        return Response(
            {
                "access": pair["access"],
                "refresh": pair["refresh"],
                "expires_in": _access_lifetime(),
                "user": UserSerializer(user).data,
            }
        )


class PinSetView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=PinSetSerializer, responses={200: None})
    def post(self, request):
        serializer = PinSetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from django.core.exceptions import ValidationError

        try:
            request.user.set_pin(serializer.validated_data["pin"])
        except ValidationError as exc:
            return Response(
                {"error": {"code": "PIN_WEAK", "message": exc.messages[0], "details": {}}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        request.user.save(update_fields=["pin_hash", "pin_set_at", "updated_at"])

        # Re-securing an account has to end the sessions opened against the old
        # credential — otherwise whoever knew the old PIN keeps their refresh
        # family. This device is spared, so setting a PIN never logs you out of
        # the phone you are holding.
        revoked = tokens.revoke_all_for_user(
            request.user,
            "PIN changed",
            except_family=getattr(request.auth, "payload", {}).get(tokens.FAMILY_CLAIM) or "",
        )
        record(
            AuditAction.PIN_SET,
            request=request,
            summary=f"PIN set; {revoked} other session(s) signed out" if revoked else "PIN set",
        )
        return Response({"status": "ok", "sessions_ended": revoked})


class TokenRefreshView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=RefreshSerializer, responses={200: None})
    def post(self, request):
        serializer = RefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            pair = tokens.rotate(
                serializer.validated_data["refresh"], tenant=getattr(request, "tenant", None)
            )
        except tokens.TokenReuseDetected:
            record(
                AuditAction.TOKEN_REUSE,
                request=request,
                summary="Refresh token replayed; session family revoked",
                succeeded=False,
            )
            return Response(
                {
                    "error": {
                        "code": "TOKEN_REUSE_DETECTED",
                        "message": "Your session was ended for security reasons. Please sign in again.",
                        "details": {},
                    }
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except TokenError as exc:
            # The reuse branch above has always been audited and this one never
            # was, which made the *common* refresh failure the invisible one:
            # nothing on the server said whether a session died of a revoked
            # family, a stale signature or a token from another school.
            claimed = tokens.verified_claims(serializer.validated_data["refresh"])
            record(
                AuditAction.TOKEN_INVALID,
                request=request,
                object_type="TokenFamily",
                object_id=claimed.get("family", ""),
                summary=_rejection_summary(exc, claimed, request),
                succeeded=False,
            )
            return Response(
                {"error": {"code": "TOKEN_INVALID", "message": str(exc), "details": {}}},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response(
            {"access": pair["access"], "refresh": pair["refresh"], "expires_in": _access_lifetime()}
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=RefreshSerializer, responses={200: None})
    def post(self, request):
        family_id = getattr(request.auth, "payload", {}).get(tokens.FAMILY_CLAIM)
        if family_id:
            with platform_context(request.user):
                tokens.revoke_family(family_id, "user logged out")
        record(AuditAction.LOGOUT, request=request, summary="Signed out")
        return Response({"status": "ok"})


class DeviceListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: DeviceSerializer(many=True)})
    def get(self, request):
        devices = request.user.devices.all()
        current = getattr(request.auth, "payload", {}).get("device_id", "")
        return Response(
            DeviceSerializer(devices, many=True, context={"current_device_id": current}).data
        )


class PushTokenView(APIView):
    """`POST /auth/devices/push-token/` — refresh this device's push token.

    FCM rotates a token whenever it feels like it (reinstall, restore, clear
    data), and the app is the only party that learns about it. Without this the
    school pays to push at an address nobody is listening on.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(request=PushTokenSerializer, responses={200: None, 404: None})
    def post(self, request):
        serializer = PushTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = request.user.devices.filter(
            device_id=serializer.validated_data["device_id"], revoked_at__isnull=True
        ).update(push_token=serializer.validated_data["push_token"])
        if not updated:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Unknown device.", "details": {}}},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({"status": "ok"})


class DeviceRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: None})
    def delete(self, request, device_id: str):
        device = request.user.devices.filter(pk=device_id).first()
        if device is None:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Unknown device.", "details": {}}},
                status=status.HTTP_404_NOT_FOUND,
            )
        device.revoke("revoked by user")
        record(AuditAction.DEVICE_REVOKED, request=request, obj=device, summary="Device revoked")
        return Response({"status": "ok"})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: UserSerializer})
    def get(self, request):
        return Response(UserSerializer(request.user).data)
