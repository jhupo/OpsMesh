from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

ACTIVE_SESSION_STATUS = "active"
ARCHIVED_SESSION_STATUS = "archived"
FROZEN_SESSION_STATUS = "frozen"
WRITABLE_SESSION_STATUSES = frozenset({ACTIVE_SESSION_STATUS})
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
        UniqueConstraint("workspace_id", "session_key", name="uq_agent_sessions_workspace_key"),
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
    openai_conversation_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    session_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ACTIVE_SESSION_STATUS)


class PersistentAgentSessionItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "persistent_agent_session_items"
    __table_args__ = (
        UniqueConstraint(
            "persistent_session_id",
            "sequence",
            name="uq_agent_session_items_session_sequence",
        ),
        Index("ix_agent_session_items_workspace_session", "workspace_id", "persistent_session_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    persistent_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("persistent_agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    item: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
