from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.app.domains.workspace.tenants.models import WorkspaceMember


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

    workspace_memberships: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    api_tokens: Mapped[list["UserAPIToken"]] = relationship(
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


class UserAPIToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_api_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_user_api_tokens_hash"),
        Index("ix_user_api_tokens_user_status", "user_id", "status"),
        Index("ix_user_api_tokens_fingerprint", "fingerprint"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(80), nullable=False)
    scopes: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="api_tokens")


class SecuredResource(TimestampMixin, Base):
    __tablename__ = "secured_resources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="RESTRICT",
        ),
        Index("ix_secured_resources_owner", "workspace_id", "owner_user_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    resource_kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    resource_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_user_id: Mapped[UUID | None] = mapped_column(nullable=True)


class ResourceGrant(TimestampMixin, Base):
    __tablename__ = "resource_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "resource_kind", "resource_id"],
            [
                "secured_resources.workspace_id",
                "secured_resources.resource_kind",
                "secured_resources.resource_id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "action IN ('read','invoke','update','delete','share','control','approve')",
            name="resource_action_valid",
        ),
        Index("ix_resource_grants_subject", "workspace_id", "user_id", "action", "resource_kind"),
    )

    workspace_id: Mapped[UUID] = mapped_column(primary_key=True)
    resource_kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    resource_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(20), primary_key=True)
