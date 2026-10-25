import hashlib
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.contracts.identity import UserIdentity
from opsmesh.infra.database import now
from opsmesh.shared.errors import ForbiddenError, NotFoundError

from .models import AccessToken, GroupMember, User


def identity(session: Session, user_id: str) -> UserIdentity:
    row = session.get(User, user_id)
    if row is None or not row.enabled:
        raise ForbiddenError("The user is unavailable")
    groups = session.scalars(select(GroupMember.group_id).where(GroupMember.user_id == user_id))
    return UserIdentity(user_id, frozenset(row.roles), frozenset(groups))


def authenticate(session: Session, token: str) -> UserIdentity:
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = session.scalar(select(AccessToken).where(AccessToken.digest == digest))
    if row is None or row.revoked or (row.expires_at is not None and row.expires_at <= now()):
        raise ForbiddenError("Invalid or expired access token")
    return identity(session, row.user_id)


def issue_token(
    session: Session, user_id: str, label: str, expires_at: datetime | None = None
) -> str:
    identity(session, user_id)
    token = "om_" + secrets.token_urlsafe(32)
    session.add(
        AccessToken(
            user_id=user_id,
            digest=hashlib.sha256(token.encode()).hexdigest(),
            label=label,
            expires_at=expires_at,
        )
    )
    return token


def require_admin(user: UserIdentity) -> None:
    if not user.is_admin:
        raise ForbiddenError("Administrator access required")


def require_owner(user: UserIdentity, owner_id: str) -> None:
    if user.user_id != owner_id and not user.is_admin:
        raise NotFoundError("Resource not found")


def can_use(session: Session, user: UserIdentity, owner_id: str) -> bool:
    if user.is_admin or user.user_id == owner_id:
        return True
    owner = session.get(User, owner_id)
    if owner is None or not owner.enabled:
        return False
    owner_groups = set(
        session.scalars(select(GroupMember.group_id).where(GroupMember.user_id == owner_id))
    )
    current = identity(session, user.user_id)
    return bool(current.group_ids & owner_groups)
