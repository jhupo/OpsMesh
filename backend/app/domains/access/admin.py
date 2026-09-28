from __future__ import annotations

from datetime import UTC, datetime
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.db.errors import DatabaseConflictError, flush_or_raise_conflict
from backend.app.core.pagination import PageParams
from backend.app.domains.access.models import User, UserAPIToken
from backend.app.domains.access.service import AuthorizationService
from backend.app.domains.workspace.tenants.models import Workspace, WorkspaceMember


class IdentityAdminService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_users(
        self,
        page: PageParams,
        *,
        status: str | None = None,
    ) -> tuple[list[User], int]:
        statement = select(User)
        count_statement = select(func.count()).select_from(User)
        if status is not None:
            statement = statement.where(User.status == status)
            count_statement = count_statement.where(User.status == status)
        total = int(self._session.scalar(count_statement) or 0)
        users = list(
            self._session.scalars(
                statement.order_by(User.created_at.desc(), User.id.desc())
                .limit(page.limit)
                .offset(page.offset)
            ).all()
        )
        return users, total

    def get_user(self, user_id: UUID) -> User | None:
        return self._session.get(User, user_id)

    def get_user_memberships(
        self,
        user_id: UUID,
    ) -> list[tuple[WorkspaceMember, Workspace]]:
        statement = (
            select(WorkspaceMember, Workspace)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .where(WorkspaceMember.user_id == user_id)
            .order_by(WorkspaceMember.created_at.desc(), WorkspaceMember.id.desc())
        )
        return [(row[0], row[1]) for row in self._session.execute(statement).all()]

    def create_user(
        self,
        *,
        email: str,
        display_name: str,
        password: str,
        username: str | None = None,
        platform_admin: bool = False,
    ) -> User:
        user = User(
            email=AuthorizationService.normalize_email(email),
            username=username.strip() if username else None,
            display_name=display_name.strip(),
            password_hash=AuthorizationService.hash_password(password),
            platform_admin=platform_admin,
        )
        self._session.add(user)
        flush_or_raise_conflict(self._session, "User email or username already exists")
        return user

    def update_user(
        self,
        user_id: UUID,
        *,
        display_name: str | None = None,
        username: str | None = None,
        platform_admin: bool | None = None,
    ) -> User | None:
        user = self._session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            return None
        if display_name is not None:
            user.display_name = display_name.strip()
        if username is not None:
            user.username = username.strip() or None
        if platform_admin is not None:
            if (
                user.platform_admin
                and not platform_admin
                and user.status == "active"
                and self._active_platform_admin_count() <= 1
            ):
                raise DatabaseConflictError("Cannot remove the last active platform administrator")
            user.platform_admin = platform_admin
        flush_or_raise_conflict(self._session, "User email or username already exists")
        return user

    def reset_password(self, user_id: UUID) -> tuple[User, str] | None:
        user = self._session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            return None
        password = token_urlsafe(32)
        user.password_hash = AuthorizationService.hash_password(password)
        now = datetime.now(UTC)
        for token in self._session.scalars(
            select(UserAPIToken).where(
                UserAPIToken.user_id == user_id,
                UserAPIToken.status == "active",
            )
        ).all():
            token.status = "revoked"
            token.revoked_at = now
        return user, password

    def revoke_tokens(self, user_id: UUID) -> int | None:
        user = self._session.get(User, user_id)
        if user is None:
            return None
        now = datetime.now(UTC)
        tokens = self._session.scalars(
            select(UserAPIToken).where(
                UserAPIToken.user_id == user_id,
                UserAPIToken.status == "active",
            )
        ).all()
        for token in tokens:
            token.status = "revoked"
            token.revoked_at = now
        return len(tokens)

    def set_user_status(self, user_id: UUID, *, status: str) -> User | None:
        user = self._session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            return None
        if (
            user.platform_admin
            and user.status == "active"
            and status == "disabled"
            and self._active_platform_admin_count() <= 1
        ):
            raise DatabaseConflictError("Cannot disable the last active platform administrator")
        user.status = status
        if status == "disabled":
            now = datetime.now(UTC)
            tokens = self._session.scalars(
                select(UserAPIToken).where(
                    UserAPIToken.user_id == user_id,
                    UserAPIToken.status == "active",
                )
            ).all()
            for token in tokens:
                token.status = "revoked"
                token.revoked_at = now
        return user

    def _active_platform_admin_count(self) -> int:
        active_admin_ids = self._session.scalars(
            select(User.id)
            .where(User.platform_admin.is_(True), User.status == "active")
            .with_for_update()
        ).all()
        return len(active_admin_ids)
