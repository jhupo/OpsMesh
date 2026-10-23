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

from opsmesh.platform.audit.contracts import AuditActor, AuditRecord, record_audit
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.serialization import canonical_json
from opsmesh.users.access import CurrentUser, PlatformRole
from opsmesh.users.avatars import normalize_avatar
from opsmesh.users.errors import DuplicateUserError
from opsmesh.users.models import (
    MAX_DISPLAY_NAME_LENGTH,
    USER_STATUS_ACTIVE,
    USER_STATUS_DELETED,
    USER_STATUSES,
    UserProfile,
)

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
    display_name = _validated_display_name(display_name)
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
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action="user.created",
            actor=actor or AuditActor(actor_type="platform"),
            source="platform",
            target_type="user",
            target_id=str(profile.id),
            summary=f"Account opened for {email}",
            payload_json=canonical_json({"platform_roles": sorted(roles)}),
        ),
    )
    return profile


def get_user(session: Session, user_id: UUID, *, lock: bool = False) -> UserProfile:
    if lock:
        return _locked_profile(session, user_id)
    profile = session.get(UserProfile, user_id)
    if profile is None:
        raise NotFoundError("The user does not exist")
    return profile


def find_by_email(session: Session, email: str, *, lock: bool = False) -> UserProfile | None:
    statement = select(UserProfile).where(UserProfile.email == email)
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement.execution_options(populate_existing=True))


def load_identity(session: Session, user_id: UUID) -> CurrentUser | None:
    profile = session.get(UserProfile, user_id, populate_existing=True)
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
) -> UserProfile:
    """Update the caller's own profile fields; nothing else is writable here."""
    profile = _locked_profile(session, user_id)
    return _apply_profile_fields(
        session,
        profile=profile,
        actor=actor,
        display_name=display_name,
    )


def apply_admin_update(
    session: Session,
    *,
    user_id: UUID,
    actor: AuditActor,
    display_name: str | None = None,
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
    if profile.status == USER_STATUS_DELETED:
        raise ConflictError("A deleted account cannot be modified")
    if status == USER_STATUS_DELETED:
        raise InvalidArgumentError("Use the account deletion operation")
    if display_name is not None:
        _apply_profile_fields(
            session,
            profile=profile,
            actor=actor,
            display_name=display_name,
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
    if status == USER_STATUS_DELETED:
        raise InvalidArgumentError("Use the account deletion operation")
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
) -> UserProfile:
    """Write the self-writable fields of an already locked profile."""
    if profile.status == USER_STATUS_DELETED:
        raise ConflictError("A deleted account cannot be modified")
    changed: list[str] = []
    if display_name is not None and display_name != profile.display_name:
        profile.display_name = _validated_display_name(display_name)
        changed.append("display_name")
    if changed:
        record_audit(
            session,
            AuditRecord(
                category="operation",
                action="user.profile_updated",
                actor=actor,
                source="platform",
                target_type="user",
                target_id=str(profile.id),
                summary="User profile updated",
                payload_json=canonical_json({"changed": changed}),
            ),
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
    if profile.status == USER_STATUS_DELETED:
        raise ConflictError("A deleted account cannot be modified")
    validated = _validated_roles(roles)
    previous = frozenset(profile.platform_roles)
    if previous == validated:
        return profile
    _guard_last_administrator(
        administrators, profile=profile, keeps_admin=PLATFORM_ADMINISTRATOR in validated
    )
    profile.platform_roles = sorted(validated)
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action="user.roles_updated",
            actor=actor,
            source="platform",
            target_type="user",
            target_id=str(profile.id),
            summary="Platform roles updated",
            payload_json=canonical_json(
                {
                    "previous_roles": sorted(previous),
                    "platform_roles": sorted(validated),
                }
            ),
        ),
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
    if profile.status == USER_STATUS_DELETED and status != USER_STATUS_DELETED:
        raise ConflictError("A deleted account cannot be reactivated")
    if profile.status == status:
        return profile
    previous_status = profile.status
    if status != USER_STATUS_ACTIVE:
        _guard_last_administrator(administrators, profile=profile, keeps_admin=False)
    profile.status = status
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action="user.status_updated",
            actor=actor,
            source="platform",
            target_type="user",
            target_id=str(profile.id),
            summary=f"User status set to {status}",
            payload_json=canonical_json({"previous_status": previous_status, "status": status}),
        ),
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
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action="user.avatar_updated",
            actor=actor,
            source="platform",
            target_type="user",
            target_id=str(profile.id),
            summary="User avatar updated",
            payload_json=canonical_json(
                {
                    "width": normalized.width,
                    "height": normalized.height,
                    "bytes": len(normalized.content),
                }
            ),
        ),
    )
    return profile


def clear_avatar(session: Session, *, user_id: UUID, actor: AuditActor) -> UserProfile:
    profile = _locked_profile(session, user_id)
    if profile.avatar_content is None:
        return profile
    profile.avatar_content = None
    profile.avatar_content_type = None
    profile.avatar_updated_at = None
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action="user.avatar_cleared",
            actor=actor,
            source="platform",
            target_type="user",
            target_id=str(profile.id),
            summary="User avatar cleared",
            payload_json=canonical_json({}),
        ),
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
        select(UserProfile)
        .where(UserProfile.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
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
            select(UserProfile)
            .where(UserProfile.id == admin_id)
            .with_for_update()
            .execution_options(populate_existing=True)
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
        if admin.status == USER_STATUS_ACTIVE and PLATFORM_ADMINISTRATOR in admin.platform_roles:
            return
    raise ConflictError(
        "The platform keeps at least one active administrator",
        details={"user_id": str(profile.id)},
    )


def _validated_display_name(value: str) -> str:
    if not value.strip() or len(value) > MAX_DISPLAY_NAME_LENGTH:
        raise InvalidArgumentError("A display name must contain between 1 and 100 characters")
    return value


def create_account(
    session: Session,
    *,
    email: str,
    display_name: str,
    password: str,
    platform_roles: frozenset[str] = frozenset({PLATFORM_MEMBER}),
    token_limit: int | None = None,
    capacity_limits: dict[str, int | None] | None = None,
    actor: AuditActor | None = None,
) -> UserProfile:
    """Create profile, password and quota accounts in the caller's transaction."""
    from opsmesh.users.auth.models import UserCredential
    from opsmesh.users.auth.passwords import hash_password
    from opsmesh.users.quotas.service import ensure_capacity_accounts, ensure_ledger

    password_hash = hash_password(password)
    if token_limit is not None and token_limit < 0:
        raise InvalidArgumentError("The token limit cannot be negative")
    profile = create_user(
        session,
        email=email,
        display_name=display_name,
        platform_roles=platform_roles,
        actor=actor,
    )
    session.add(UserCredential(user_id=profile.id, password_hash=password_hash))
    ensure_ledger(
        session,
        user_id=profile.id,
        token_limit=token_limit,
    )
    if capacity_limits is not None:
        ensure_capacity_accounts(session, user_id=profile.id, limits=capacity_limits)
    return profile


def delete_account(session: Session, *, user_id: UUID, actor: AuditActor) -> UserProfile:
    """Tombstone an account while retaining references and outstanding usage."""
    from opsmesh.users.auth.models import LoginSession, UserCredential

    administrators = _lock_active_administrators(session)
    profile = _locked_profile(session, user_id)
    if profile.status == USER_STATUS_DELETED:
        return profile
    _apply_status(
        session,
        profile=profile,
        administrators=administrators,
        status=USER_STATUS_DELETED,
        actor=actor,
    )
    credential = session.scalar(
        select(UserCredential).where(UserCredential.user_id == user_id).with_for_update()
    )
    if credential is not None:
        session.delete(credential)
    now = datetime.now(UTC)
    for login in session.scalars(
        select(LoginSession).where(
            LoginSession.user_id == user_id, LoginSession.revoked_at.is_(None)
        )
    ):
        login.revoked_at = now
    profile.avatar_content = None
    profile.avatar_content_type = None
    profile.avatar_updated_at = None
    return profile
