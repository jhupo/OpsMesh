from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentMessageThread(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_message_threads"
    __table_args__ = (
        Index("ix_agent_message_threads_workspace_team", "workspace_id", "agent_team_id"),
        Index("ix_agent_message_threads_workspace_task", "workspace_id", "task_id"),
        Index("ix_agent_message_threads_workspace_status", "workspace_id", "status"),
        Index("ix_agent_message_threads_workspace_updated", "workspace_id", "updated_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    subject: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    messages: Mapped[list["AgentMessage"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
    )


class AgentMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        Index("ix_agent_messages_workspace_thread", "workspace_id", "thread_id"),
        Index("ix_agent_messages_workspace_team", "workspace_id", "agent_team_id"),
        Index("ix_agent_messages_workspace_task", "workspace_id", "task_id"),
        Index("ix_agent_messages_workspace_sender", "workspace_id", "sender_agent_profile_id"),
        Index(
            "ix_agent_messages_workspace_recipient_status",
            "workspace_id",
            "recipient_agent_profile_id",
            "status",
        ),
        Index("ix_agent_messages_workspace_type", "workspace_id", "message_type"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_message_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    sender_agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    recipient_agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    reply_to_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    message_type: Mapped[str] = mapped_column(String(80), nullable=False, default="message")
    body: Mapped[str] = mapped_column(String, nullable=False, default="")
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="sent")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    thread: Mapped[AgentMessageThread] = relationship(back_populates="messages")
