from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from opsmesh.workspaces.members.models import WorkspaceMember


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opsmesh.identity.auth.models import UserAPIToken


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        UniqueConstraint("username", name="uq_users_username"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    avatar_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    workspace_memberships: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    api_tokens: Mapped[list[UserAPIToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserAvatar(Base):
    __tablename__ = "user_avatars"
    __table_args__ = (
        CheckConstraint("length(content) BETWEEN 1 AND 262144", name="avatar_content_size"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
