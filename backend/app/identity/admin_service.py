from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.identity.models import User, UserAPIToken


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

    def set_user_status(self, user_id: UUID, *, status: str) -> User | None:
        user = self._session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            return None
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
        self._session.commit()
        self._session.refresh(user)
        return user
