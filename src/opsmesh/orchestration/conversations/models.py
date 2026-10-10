from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_owner", "workspace_id", "created_by_user_id"),)

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(240))
    mode: Mapped[str] = mapped_column(String(16))
    agent_profile_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_profiles.id"))
    agent_team_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_teams.id"))
    runtime_space_id: Mapped[UUID | None] = mapped_column(ForeignKey("runtime_spaces.id"))
    workspace_project_id: Mapped[UUID | None] = mapped_column(ForeignKey("workspace_projects.id"))
    orchestration_definition_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("orchestration_definitions.id")
    )
    orchestration_version: Mapped[int | None] = mapped_column(Integer)
    next_sequence: Mapped[int] = mapped_column(Integer, default=1)


class ConversationTurn(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint("conversation_id", "idempotency_key", name="uq_conversation_turns_key"),
        UniqueConstraint("conversation_id", "sequence", name="uq_conversation_turns_sequence"),
        Index("ix_conversation_turns_status", "status", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    sequence: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(String)
    reply: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    error_code: Mapped[str | None] = mapped_column(String(120))
    execution_identity: Mapped[dict[str, object]] = mapped_column(JSONB)
    round: Mapped[int] = mapped_column(Integer, default=0)


class ConversationExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversation_executions"
    __table_args__ = (UniqueConstraint("turn_id", "key"), UniqueConstraint("task_id"))

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_turns.id", ondelete="CASCADE"))
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    key: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(16))
    parent_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    round: Mapped[int] = mapped_column(Integer)


class ConversationEvent(TimestampMixin, Base):
    __tablename__ = "conversation_events"
    __table_args__ = (
        Index("ix_conversation_events_cursor", "workspace_id", "conversation_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_turns.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32))
    round: Mapped[int] = mapped_column(Integer)


def turn_event(turn: ConversationTurn) -> ConversationEvent:
    return ConversationEvent(
        workspace_id=turn.workspace_id,
        conversation_id=turn.conversation_id,
        turn_id=turn.id,
        status=turn.status,
        round=turn.round,
    )
