from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Skill(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("key", "version", name="uq_skills_key_version"),
        Index("ix_skills_status", "status"),
    )

    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False, default="1.0.0")
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    capability_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    manifest: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    owner_workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="public")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class WorkspaceSkillInstall(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_skill_installs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "skill_id", name="uq_workspace_skill_installs_skill"),
        Index("ix_workspace_skill_installs_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_id: Mapped[UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    installed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    installed_key: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    installed_name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    installed_version: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    installed_description: Mapped[str] = mapped_column(String, nullable=False, default="")
    installed_capability_keys: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    installed_manifest: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    source_owner_workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="public")
    source_checksum: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    config: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
