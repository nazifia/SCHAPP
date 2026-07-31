"""DRF permission classes built on the code-declared catalogue."""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from .permissions_catalog import is_known


def RequirePermission(*codes: str, require_all: bool = False):  # noqa: N802 - class factory
    """`permission_classes = [RequirePermission("assessment.enter_score")]`."""
    for code in codes:
        if not is_known(code):
            raise ValueError(f"Unknown permission code: {code}")

    class _RequirePermission(BasePermission):
        message = "You do not have permission to perform this action."

        def has_permission(self, request, view) -> bool:
            user = request.user
            if not (user and user.is_authenticated):
                return False
            check = all if require_all else any
            return check(user.has_perm_code(code) for code in codes)

    _RequirePermission.__name__ = f"RequirePermission({', '.join(codes)})"
    return _RequirePermission


class IsSelfOrHasPermission(BasePermission):
    """Object-level: a user may always read their own record.

    Views set `owner_field` (default "user"); staff paths still need the
    permission the view declares.
    """

    owner_field = "user"

    def has_object_permission(self, request, view, obj) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        owner = getattr(obj, getattr(view, "owner_field", self.owner_field), None)
        if owner == user or obj == user:
            return True
        return request.method in SAFE_METHODS and user.has_perm_code("people.view_staff")
