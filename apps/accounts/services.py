import logging

from django.utils import timezone

from apps.api.exceptions import AppError
from apps.numbering.msisdn import format_display, normalize
from apps.tenants.db import tenant_atomic

from .models import Device, Role, User
from .permissions_catalog import SYSTEM_ROLES

logger = logging.getLogger(__name__)


@tenant_atomic()
def seed_roles() -> int:
    """Create the system roles in the current schema. Idempotent."""
    created = 0
    for code, (name, permissions) in SYSTEM_ROLES.items():
        role, was_created = Role.objects.get_or_create(
            code=code, defaults={"name": name, "permissions": permissions, "is_system": True}
        )
        if not was_created and role.is_system and role.permissions != permissions:
            # Keep shipped roles current without clobbering custom ones.
            role.permissions = permissions
            role.name = name
            role.save(update_fields=["permissions", "name", "updated_at"])
        created += was_created
    return created


def get_or_create_user(phone: str, **defaults) -> tuple[User, bool]:
    canonical = normalize(phone)
    user = User.objects.filter(phone=canonical).first()
    if user:
        return user, False
    return (
        User.objects.create_user(canonical, phone_display=format_display(canonical), **defaults),
        True,
    )


def assign_role(user: User, role_code: str) -> None:
    role = Role.objects.get(code=role_code)
    user.roles.add(role)


def set_roles(user: User, codes: list[str], *, request=None) -> None:
    """Replace a user's roles with exactly `codes`, and audit the change.

    The whole set, not a delta: "add" and "remove" leave two clients
    disagreeing about what a user ended up with. Lives here rather than in the
    admin view because staff onboarding grants roles too, and a privilege
    change that skipped the audit record depending on which screen made it
    would be worse than no record at all.
    """
    from apps.audit.models import AuditAction
    from apps.audit.services import record

    roles = list(Role.objects.filter(code__in=codes))
    missing = sorted(set(codes) - {role.code for role in roles})
    if missing:
        raise AppError(
            f"No such role(s): {', '.join(missing)}.",
            code="UNKNOWN_ROLE",
            details={"unknown": missing},
        )

    before = sorted(user.roles.values_list("code", flat=True))
    user.roles.set(roles)
    after = sorted(codes)
    if before != after:
        record(
            AuditAction.ROLE_ASSIGNED,
            request=request,
            obj=user,
            summary=f"Roles for {user.phone_display}: {', '.join(after) or 'none'}",
            before={"roles": before},
            after={"roles": after},
        )


def register_device(user: User, *, ip: str | None = None, **info) -> tuple:
    """Upsert a device. Returns (device, is_new) — a new one triggers a notice."""
    device_id = info.pop("device_id", "")
    if not device_id:
        return None, False

    device, is_new = Device.objects.get_or_create(
        user=user,
        device_id=device_id,
        defaults={
            "name": info.get("name", ""),
            "platform": info.get("platform", ""),
            "app_version": info.get("app_version", ""),
            "push_token": info.get("push_token", ""),
        },
    )
    device.last_seen_at = timezone.now()
    device.last_ip = ip
    if info.get("push_token"):
        device.push_token = info["push_token"]
    if device.revoked_at and is_new is False:
        # A revoked device logging in again is a fresh trust decision.
        device.revoked_at = None
        is_new = True
    device.save(update_fields=["last_seen_at", "last_ip", "push_token", "revoked_at", "updated_at"])
    return device, is_new
