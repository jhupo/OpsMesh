from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

ACTIVE_SESSION_STATUS = "active"
ARCHIVED_SESSION_STATUS = "archived"
FROZEN_SESSION_STATUS = "frozen"
PERSISTENT_AGENT_SESSION_STATUSES = frozenset(
    {ACTIVE_SESSION_STATUS, ARCHIVED_SESSION_STATUS, FROZEN_SESSION_STATUS}
)


@dataclass(frozen=True)
class PersistentAgentSessionRef:
    session_key: str
    workspace_id: UUID
    scope_type: str
    scope_id: str


class PersistentAgentSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "persistent_agent_sessions"
    __table_args__ = (
        UniqueConstraint("session_key", name="uq_agent_sessions_key"),
        Index("ix_agent_sessions_workspace_scope", "workspace_id", "scope_type", "scope_id"),
        Index("ix_agent_sessions_workspace_updated", "workspace_id", "updated_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_key: Mapped[str] = mapped_column(String(240), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(80), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(240), nullable=False)
    agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_teams.id", ondelete="CASCADE"),
        nullable=True,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=True,
    )
    session_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ACTIVE_SESSION_STATUS)


class SDKAgentSession(Base):
    """Read model for the SDK-owned SQLAlchemySession table."""

    __tablename__ = "sdk_agent_sessions"
    session_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("persistent_agent_sessions.session_key", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class SDKAgentMessage(Base):
    """Governance queries read the SDK table; history writes belong to the SDK."""

    __tablename__ = "sdk_agent_messages"
    __table_args__ = (
        Index("idx_sdk_agent_messages_session_time", "session_id", "created_at"),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("sdk_agent_sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    message_data: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
