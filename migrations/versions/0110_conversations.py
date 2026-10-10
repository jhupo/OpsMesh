"""Durable private conversations and their task execution links."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0110_conversations"
down_revision = "0109_platform_dashboard"
branch_labels = None
depends_on = None


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _ref("workspace_id", "workspaces", delete="CASCADE"),
    ]


def _ref(name: str, table: str, *, nullable: bool = False, delete: str | None = None) -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(f"{table}.id", ondelete=delete),
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "conversations",
        *_base(),
        _ref("created_by_user_id", "users", delete="CASCADE"),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        _ref("agent_profile_id", "agent_profiles", nullable=True),
        _ref("agent_team_id", "agent_teams", nullable=True),
        _ref("runtime_space_id", "runtime_spaces", nullable=True),
        _ref("workspace_project_id", "workspace_projects", nullable=True),
        sa.Column("next_sequence", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_conversations_owner", "conversations", ["workspace_id", "created_by_user_id"]
    )
    op.create_table(
        "conversation_turns",
        *_base(),
        _ref("conversation_id", "conversations", delete="CASCADE"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("reply", sa.String(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("execution_identity", postgresql.JSONB(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.UniqueConstraint("conversation_id", "idempotency_key", name="uq_conversation_turns_key"),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_conversation_turns_sequence"),
    )
    op.create_index("ix_conversation_turns_status", "conversation_turns", ["status", "created_at"])
    op.create_table(
        "conversation_executions",
        *_base(),
        _ref("turn_id", "conversation_turns", delete="CASCADE"),
        _ref("task_id", "tasks"),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        _ref("parent_run_id", "agent_runs", nullable=True),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.UniqueConstraint("turn_id", "key"),
        sa.UniqueConstraint("task_id"),
    )

    op.create_table(
        "conversation_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _ref("workspace_id", "workspaces", delete="CASCADE"),
        _ref("conversation_id", "conversations", delete="CASCADE"),
        _ref("turn_id", "conversation_turns", delete="CASCADE"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_conversation_events_cursor",
        "conversation_events",
        ["workspace_id", "conversation_id", "id"],
    )


def downgrade() -> None:
    op.drop_table("conversation_events")
    op.drop_table("conversation_executions")
    op.drop_table("conversation_turns")
    op.drop_table("conversations")
