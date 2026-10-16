"""User service: profile, platform roles, status and avatar operations.

Every mutating operation records its platform audit fact inside the caller's
transaction; role and status changes are authorized by the route layer, which
owns the platform-role check. Every mutation locks the rows it writes — the
administrator boundary and the target profile — before its first audit
append, so no two user updates can hold the audit chain head while waiting
for each other's row locks.
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
    profile = _locked_profile(session, user_id)
    return _apply_profile_fields(
        session,
        profile=profile,
        actor=actor,
        display_name=display_name,
        work_root=work_root,
    )


def apply_admin_update(
    session: Session,
    *,
    user_id: UUID,
    actor: AuditActor,
    display_name: str | None = None,
    work_root: str | None = None,
    status: str | None = None,
    roles: frozenset[str] | None = None,
) -> UserProfile:
    """Apply one administrative update under one lock order.

    The administrator boundary and the target profile are locked before the
    first audit append, whatever fields the request carries: a combined
    update, a roles-only update and a profile-only update then all take
    their row locks before the audit chain head, so no two of them can hold
    the chain head while waiting for each other's rows.
    """
    # Every declared field is validated before the first lock or mutation:
    # an unknown status must be refused here, exactly like in set_status, or
    # an invalid value would be written past the last-administrator guard.
    if status is not None:
        status = _validated_status(status)
    if roles is not None:
        roles = _validated_roles(roles)
    administrators = _lock_active_administrators(session)
    profile = _locked_profile(session, user_id)
    if display_name is not None or work_root is not None:
        _apply_profile_fields(
            session,
            profile=profile,
            actor=actor,
            display_name=display_name,
            work_root=work_root,
        )
    if status is not None:
        _apply_status(
            session,
            profile=profile,
            administrators=administrators,
            status=status,
            actor=actor,
        )
    if roles is not None:
        _apply_roles(
            session,
            profile=profile,
            administrators=administrators,
            roles=roles,
            actor=actor,
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
    # The administrator set is the serialization boundary of the last-admin
    # guard: every role or status change locks it first, in one global order,
    # so two transactions cannot each see the other administrator and both
    # remove the last ones.
    administrators = _lock_active_administrators(session)
    profile = _locked_profile(session, user_id)
    return _apply_roles(
        session, profile=profile, administrators=administrators, roles=roles, actor=actor
    )


def set_status(
    session: Session,
    *,
    user_id: UUID,
    status: str,
    actor: AuditActor,
) -> UserProfile:
    """Enable or disable one account; a disabled account cannot authenticate."""
    status = _validated_status(status)
    administrators = _lock_active_administrators(session)
    profile = _locked_profile(session, user_id)
    return _apply_status(
        session,
        profile=profile,
        administrators=administrators,
        status=status,
        actor=actor,
    )


def _apply_profile_fields(
    session: Session,
    *,
    profile: UserProfile,
    actor: AuditActor,
    display_name: str | None,
    work_root: str | None,
) -> UserProfile:
    """Write the self-writable fields of an already locked profile."""
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


def _apply_roles(
    session: Session,
    *,
    profile: UserProfile,
    administrators: list[UserProfile],
    roles: frozenset[str],
    actor: AuditActor,
) -> UserProfile:
    """Replace the platform roles of an already locked profile."""
    validated = _validated_roles(roles)
    previous = frozenset(profile.platform_roles)
    if previous == validated:
        return profile
    _guard_last_administrator(
        administrators, profile=profile, keeps_admin=PLATFORM_ADMINISTRATOR in validated
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


def _apply_status(
    session: Session,
    *,
    profile: UserProfile,
    administrators: list[UserProfile],
    status: str,
    actor: AuditActor,
) -> UserProfile:
    """Set the account status of an already locked profile.

    The caller validates the status; any status that is not active takes the
    account out of service, so the last-administrator guard covers it, not
    only the literal ``disabled`` value.
    """
    if profile.status == status:
        return profile
    previous_status = profile.status
    if status != USER_STATUS_ACTIVE:
        _guard_last_administrator(administrators, profile=profile, keeps_admin=False)
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
    # The row is locked before the audit append: a mutation that took the
    # audit chain head first and the row lock at flush could deadlock against
    # a concurrent role change that holds the row and waits for the head.
    profile = _locked_profile(session, user_id)
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
    profile = _locked_profile(session, user_id)
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


def _lock_active_administrators(session: Session) -> list[UserProfile]:
    """Lock every active administrator, one row at a time in id order.

    The per-id acquisition order is deliberate: a single ``FOR UPDATE``
    query with ``ORDER BY`` locks rows in plan order, which two transactions
    could traverse in opposite directions and deadlock. Locking the ids in
    ascending order gives every role or status change the same boundary, and
    PostgreSQL re-reads each row after a lock wait, so the guard below sees
    the committed state of every administrator.
    """
    admin_ids = list(
        session.scalars(
            select(UserProfile.id)
            .where(
                UserProfile.status == USER_STATUS_ACTIVE,
                UserProfile.platform_roles.contains([PLATFORM_ADMINISTRATOR]),
            )
            .order_by(UserProfile.id)
        )
    )
    locked: list[UserProfile] = []
    for admin_id in admin_ids:
        row = session.scalar(
            select(UserProfile).where(UserProfile.id == admin_id).with_for_update()
        )
        if row is not None:
            locked.append(row)
    return locked


def _validated_roles(roles: frozenset[str]) -> frozenset[str]:
    allowed = {str(role) for role in PlatformRole}
    unknown = sorted(roles - allowed)
    if unknown:
        raise InvalidArgumentError(
            "The platform roles are unknown", details={"unknown_roles": unknown}
        )
    return frozenset(roles)


def _validated_status(status: str) -> str:
    """Refuse a status the account lifecycle does not define.

    Both status entries — the standalone setter and the administrative
    update — validate through here before any lock or mutation, so an
    unknown value can never reach the last-administrator guard or the
    stored profile.
    """
    if status not in USER_STATUSES:
        raise InvalidArgumentError(
            "The user status is unknown",
            details={"known_statuses": list(USER_STATUSES)},
        )
    return status


def _guard_last_administrator(
    administrators: list[UserProfile], *, profile: UserProfile, keeps_admin: bool
) -> None:
    """Refuse a change that would leave the platform without an administrator.

    ``administrators`` is the locked set, and every row in it was re-read
    after any lock wait, so the decision uses each row's current values
    instead of its identity: another administrator that a concurrent
    transaction already demoted or disabled no longer counts, and two
    transactions cannot both observe each other and both pass.
    """
    if keeps_admin or PLATFORM_ADMINISTRATOR not in profile.platform_roles:
        return
    if profile.status != USER_STATUS_ACTIVE:
        return
    for admin in administrators:
        if admin.id == profile.id:
            continue
        if (
            admin.status == USER_STATUS_ACTIVE
            and PLATFORM_ADMINISTRATOR in admin.platform_roles
        ):
            return
    raise ConflictError(
        "The platform keeps at least one active administrator",
        details={"user_id": str(profile.id)},
    )
