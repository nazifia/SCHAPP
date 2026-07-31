"""User and role administration.

`admin.manage_users` and `admin.manage_roles` have been in the catalogue since
Phase 2 with nothing checking them, which by this codebase's own rule ("a
permission that no code checks is a lie") made them lies: the only way to give
a teacher their teacher role was the Django admin, which needs a staff account
on the school's own database. This is the API that makes them true.

Role assignment is the privilege boundary in this application — a role is a
list of permission codes, so granting one grants everything on it. That is
what `admin.manage_users` means, so the control is not a further restriction
but the audit record every change writes.
"""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.response import Response

from apps.api.exceptions import AppError
from apps.api.mixins import TenantScopedViewSet

from . import services
from .models import Role, User
from .permissions import RequirePermission
from .permissions_catalog import ALL, PERMISSIONS, is_known
from .serializers import RoleSerializer, UserListSerializer, UserSerializer

ManageUsers = RequirePermission("admin.manage_users")
ManageRoles = RequirePermission("admin.manage_roles")


class UserWriteSerializer(serializers.Serializer):
    """Creating an account, not registering one.

    No PIN and no password: the office creates the record, the holder signs in
    with an OTP to their own phone and sets their own PIN. An administrator who
    could set someone's credential could sign in as them.
    """

    phone = serializers.CharField()
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    other_name = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    roles = serializers.ListField(child=serializers.CharField(), required=False)


class RoleAssignmentSerializer(serializers.Serializer):
    """The whole set, not a delta: "add" and "remove" leave two clients
    disagreeing about what a user ended up with."""

    roles = serializers.ListField(child=serializers.CharField())


class UserViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    queryset = User.objects.prefetch_related("roles").order_by("last_name", "first_name")
    permission_classes = [ManageUsers]
    filterset_fields = ["is_active", "roles__code"]
    #: Both number columns, because they are grouped differently: a search for
    #: "0803 222" matches the display form, one for "8032222222" the E.164.
    search_fields = ["first_name", "last_name", "phone_display", "phone"]
    http_method_names = ["get", "post", "patch", "put", "head", "options"]

    def get_serializer_class(self):
        if self.action == "list":
            return UserListSerializer
        if self.action in {"create", "partial_update"}:
            return UserWriteSerializer
        return UserSerializer

    def create(self, request, *args, **kwargs):
        serializer = UserWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        roles = data.pop("roles", [])
        phone = data.pop("phone")

        try:
            user, created = services.get_or_create_user(phone, **data)
        except Exception as exc:  # InvalidMsisdn carries its own code
            raise _as_app_error(exc) from exc

        if roles:
            services.set_roles(user, roles, request=request)
        return Response(UserSerializer(user).data, status=201 if created else 200)

    def partial_update(self, request, *args, **kwargs):
        """Names, email and `is_active` only. Phone is identity here — an
        account whose number changed is a different person until an OTP to the
        new number says otherwise."""
        user = self.get_object()
        for field in ("first_name", "last_name", "other_name", "email"):
            if field in request.data:
                setattr(user, field, request.data[field])
        deactivated = False
        if "is_active" in request.data:
            active = bool(request.data["is_active"])
            deactivated = user.is_active and not active
            user.is_active = active
        user.save()
        if deactivated:
            # Authentication already refuses an inactive user, so this is about
            # the refresh families: left alive, reactivating the account months
            # later silently hands every old device its session back.
            from apps.auth_phone import tokens

            tokens.revoke_all_for_user(user, "account deactivated")
        return Response(UserSerializer(user).data)

    def update(self, request, *args, **kwargs):
        """PUT is enabled for `roles/` below; on the record itself it is not.

        A full replace would blank every field the caller left out, and one of
        those fields is the phone number this account signs in with.
        """
        raise MethodNotAllowed("PUT", detail="Use PATCH to edit a user.")

    @extend_schema(request=RoleAssignmentSerializer, responses={200: UserSerializer})
    @action(detail=True, methods=["put"])
    def roles(self, request, pk=None):
        user = self.get_object()
        serializer = RoleAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.set_roles(user, serializer.validated_data["roles"], request=request)
        return Response(UserSerializer(user).data)


def _as_app_error(exc: Exception) -> Exception:
    code = getattr(exc, "code", None)
    if code:
        return AppError(getattr(exc, "message", str(exc)), code=code)
    return exc


class RoleViewSet(TenantScopedViewSet, viewsets.ModelViewSet):
    """Roles are per-school data; the permission *codes* on them are not.

    A school may invent "Head of Exams" and hand it whichever codes it likes,
    but only codes the application actually checks — an invented one would look
    like a granted power and be silently ignored.
    """

    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_classes = [ManageRoles]
    lookup_field = "code"

    def perform_create(self, serializer):
        _validate_codes(serializer.validated_data.get("permissions", []))
        serializer.save(is_system=False)

    def perform_update(self, serializer):
        _validate_codes(serializer.validated_data.get("permissions", []))
        serializer.save()

    def perform_destroy(self, instance):
        # The seeded roles are re-created by provisioning anyway, and deleting
        # one silently strips every user who holds it.
        if instance.is_system:
            raise AppError(
                f"{instance.name} is a system role and cannot be deleted.",
                code="SYSTEM_ROLE",
            )
        instance.delete()

    @extend_schema(responses={200: None}, description="Every permission code the app checks.")
    @action(detail=False, methods=["get"])
    def catalogue(self, request):
        """So a role editor offers the real list instead of a free-text box."""
        return Response(
            {
                "wildcard": ALL,
                "permissions": [
                    {"code": code, "description": description}
                    for code, description in sorted(PERMISSIONS.items())
                ],
            }
        )


def _validate_codes(codes) -> None:
    unknown = [code for code in codes or [] if not is_known(code)]
    if unknown:
        raise AppError(
            f"Unknown permission code(s): {', '.join(unknown)}.",
            code="UNKNOWN_PERMISSION",
            details={"unknown": unknown},
        )
