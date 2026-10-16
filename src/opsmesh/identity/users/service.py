"""User service: profile, platform roles, status and avatar operations.

Every mutating operation records its platform audit fact inside the caller's
transaction; role and status changes are authorized by the route layer, which
owns the platform-role check.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.identity.access import PlatformRole
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.errors import DuplicateUserError
from opsmesh.identity.users.avatars import normalize_avatar
from opsmesh.identity.users.models import (
    USER_STATUS_ACTIVE,
    USER_STATUS_DISABLED,
    USER_STATUSES,
    UserProfile,
)
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams

PLATFORM_ADMINISTRATOR = str(PlatformRole.ADMIN)
PLATFORM_MEMBER = str(PlatformRole.MEMBER)


def create_user(
    session: Session,
    *,
    email: str,
    display_name: str,
    platform_roles: frozenset[str] = frozenset({PLATFORM_MEMBER}),
    actor: AuditActor | None = None,
) -> UserProfile:
    """Create one account profile with its platform roles."""
    roles = _validated_roles(platform_roles)
    if session.scalar(select(UserProfile).where(UserProfile.email == email)) is not None:
        raise DuplicateUserError(details={"email_domain": email.rsplit("@", 1)[-1]})
    profile = UserProfile(
        email=email,
        display_name=display_name,
        status=USER_STATUS_ACTIVE,
        platform_roles=sorted(roles),
    )
    session.add(profile)
    try:
        session.flush()
    except IntegrityError as exc:
        raise DuplicateUserError() from exc
    append_platform_event(
        session,
        actor=actor or AuditActor(actor_type="platform"),
        action="user.created",
        target_type="user",
        target_id=str(profile.id),
        summary=f"Account opened for {email}",
        details={"platform_roles": sorted(roles)},
    )
    return profile


def get_user(session: Session, user_id: UUID) -> UserProfile:
    profile = session.get(UserProfile, user_id)
    if profile is None:
        raise NotFoundError("The user does not exist")
    return profile


def find_by_email(session: Session, email: str) -> UserProfile | None:
    return session.scalar(select(UserProfile).where(UserProfile.email == email))


def load_identity(session: Session, user_id: UUID) -> CurrentUser | None:
    profile = session.get(UserProfile, user_id)
    if profile is None:
        return None
    return identity_of(profile)


def list_users(
    session: Session,
    *,
    page: PageParams,
    status: str | None = None,
    email_query: str | None = None,
) -> tuple[list[UserProfile], int]:
    """One offset page of profiles plus the matching total count."""
    if status is not None and status not in USER_STATUSES:
        raise InvalidArgumentError("The user status filter is unknown")
    query = select(UserProfile)
    count_query = select(func.count()).select_from(UserProfile)
    if status is not None:
        query = query.where(UserProfile.status == status)
        count_query = count_query.where(UserProfile.status == status)
    if email_query is not None:
        pattern = f"%{email_query}%"
        query = query.where(UserProfile.email.ilike(pattern))
        count_query = count_query.where(UserProfile.email.ilike(pattern))
    total = session.scalar(count_query) or 0
    rows = list(
        session.scalars(
            query.order_by(UserProfile.created_at.asc(), UserProfile.id.asc())
            .limit(page.limit)
            .offset(page.offset)
        )
    )
    return rows, total


def update_profile(
    session: Session,
    *,
    user_id: UUID,
    actor: AuditActor,
    display_name: str | None = None,
    work_root: str | None = None,
) -> UserProfile:
    """Update the caller's own profile fields; nothing else is writable here."""
    profile = session.get(UserProfile, user_id)
    if profile is None:
        raise NotFoundError("The user does not exist")
    changed: list[str] = []
    if display_name is not None and display_name != profile.display_name:
        profile.display_name = display_name
        changed.append("display_name")
    if work_root is not None and work_root != profile.work_root:
        profile.work_root = work_root
        changed.append("work_root")
    if changed:
        append_platform_event(
            session,
            actor=actor,
            action="user.profile_updated",
            target_type="user",
            target_id=str(profile.id),
            summary="User profile updated",
            details={"changed": changed},
        )
    return profile


def set_platform_roles(
    session: Session,
    *,
    user_id: UUID,
    roles: frozenset[str],
    actor: AuditActor,
) -> UserProfile:
    """Replace one account's platform roles; the last admin is protected."""
    validated = _validated_roles(roles)
    profile = _locked_profile(session, user_id)
    previous = frozenset(profile.platform_roles)
    if previous == validated:
        return profile
    _guard_last_administrator(
        session, profile=profile, keeps_admin=PLATFORM_ADMINISTRATOR in validated
    )
    profile.platform_roles = sorted(validated)
    append_platform_event(
        session,
        actor=actor,
        action="user.roles_updated",
        target_type="user",
        target_id=str(profile.id),
        summary="Platform roles updated",
        details={
            "previous_roles": sorted(previous),
            "platform_roles": sorted(validated),
        },
    )
    return profile


def set_status(
    session: Session,
    *,
    user_id: UUID,
    status: str,
    actor: AuditActor,
) -> UserProfile:
    """Enable or disable one account; a disabled account cannot authenticate."""
    if status not in USER_STATUSES:
        raise InvalidArgumentError("The user status is unknown")
    profile = _locked_profile(session, user_id)
    if profile.status == status:
        return profile
    previous_status = profile.status
    if status == USER_STATUS_DISABLED:
        _guard_last_administrator(session, profile=profile, keeps_admin=False)
    profile.status = status
    append_platform_event(
        session,
        actor=actor,
        action="user.status_updated",
        target_type="user",
        target_id=str(profile.id),
        summary=f"User status set to {status}",
        details={"previous_status": previous_status, "status": status},
    )
    return profile


def set_avatar(
    session: Session,
    *,
    user_id: UUID,
    content: bytes,
    content_type: str,
    actor: AuditActor,
) -> UserProfile:
    """Normalize and store one avatar on the profile itself."""
    normalized = normalize_avatar(content, content_type=content_type)
    profile = session.get(UserProfile, user_id)
    if profile is None:
        raise NotFoundError("The user does not exist")
    profile.avatar_content = normalized.content
    profile.avatar_content_type = normalized.content_type
    profile.avatar_updated_at = datetime.now(UTC)
    append_platform_event(
        session,
        actor=actor,
        action="user.avatar_updated",
        target_type="user",
        target_id=str(profile.id),
        summary="User avatar updated",
        details={
            "width": normalized.width,
            "height": normalized.height,
            "bytes": len(normalized.content),
        },
    )
    return profile


def clear_avatar(session: Session, *, user_id: UUID, actor: AuditActor) -> UserProfile:
    profile = session.get(UserProfile, user_id)
    if profile is None:
        raise NotFoundError("The user does not exist")
    if profile.avatar_content is None:
        return profile
    profile.avatar_content = None
    profile.avatar_content_type = None
    profile.avatar_updated_at = None
    append_platform_event(
        session,
        actor=actor,
        action="user.avatar_cleared",
        target_type="user",
        target_id=str(profile.id),
        summary="User avatar cleared",
        details={},
    )
    return profile


def identity_of(profile: UserProfile) -> CurrentUser:
    """The authentication value of one profile; platform roles travel with it."""
    return CurrentUser(
        user_id=profile.id,
        email=profile.email,
        display_name=profile.display_name,
        status=profile.status,
        platform_roles=frozenset(profile.platform_roles),
    )


def _locked_profile(session: Session, user_id: UUID) -> UserProfile:
    profile = session.scalar(
        select(UserProfile).where(UserProfile.id == user_id).with_for_update()
    )
    if profile is None:
        raise NotFoundError("The user does not exist")
    return profile


def _validated_roles(roles: frozenset[str]) -> frozenset[str]:
    allowed = {str(role) for role in PlatformRole}
    unknown = sorted(roles - allowed)
    if unknown:
        raise InvalidArgumentError(
            "The platform roles are unknown", details={"unknown_roles": unknown}
        )
    return frozenset(roles)


def _guard_last_administrator(
    session: Session, *, profile: UserProfile, keeps_admin: bool
) -> None:
    """Refuse a change that would leave the platform without an administrator."""
    if keeps_admin or PLATFORM_ADMINISTRATOR not in profile.platform_roles:
        return
    if profile.status != USER_STATUS_ACTIVE:
        return
    other_admins = session.scalar(
        select(func.count())
        .select_from(UserProfile)
        .where(
            UserProfile.id != profile.id,
            UserProfile.status == USER_STATUS_ACTIVE,
            UserProfile.platform_roles.contains([PLATFORM_ADMINISTRATOR]),
        )
    )
    if not other_admins:
        raise ConflictError(
            "The platform keeps at least one active administrator",
            details={"user_id": str(profile.id)},
        )
