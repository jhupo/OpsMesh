"""create agent message mailbox

Revision ID: 0041_agent_message_mailbox
Revises: 0040_audit_event_hash_chain
Create Date: 2026-06-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_agent_message_mailbox"
down_revision: str | None = "0040_audit_event_hash_chain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_message_threads",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_message_threads")),
    )
    op.create_index(
        "ix_agent_message_threads_workspace_status",
        "agent_message_threads",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_agent_message_threads_workspace_task",
        "agent_message_threads",
        ["workspace_id", "task_id"],
    )
    op.create_index(
        "ix_agent_message_threads_workspace_updated",
        "agent_message_threads",
        ["workspace_id", "updated_at"],
    )

    op.create_table(
        "agent_messages",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sender_agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recipient_agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reply_to_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message_type", sa.String(length=80), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["recipient_agent_profile_id"], ["agent_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reply_to_message_id"], ["agent_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sender_agent_profile_id"], ["agent_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["thread_id"], ["agent_message_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_messages")),
    )
    op.create_index(
        "ix_agent_messages_workspace_recipient_status",
        "agent_messages",
        ["workspace_id", "recipient_agent_profile_id", "status"],
    )
    op.create_index(
        "ix_agent_messages_workspace_sender",
        "agent_messages",
        ["workspace_id", "sender_agent_profile_id"],
    )
    op.create_index(
        "ix_agent_messages_workspace_task",
        "agent_messages",
        ["workspace_id", "task_id"],
    )
    op.create_index(
        "ix_agent_messages_workspace_thread",
        "agent_messages",
        ["workspace_id", "thread_id"],
    )
    op.create_index(
        "ix_agent_messages_workspace_type",
        "agent_messages",
        ["workspace_id", "message_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_messages_workspace_type", table_name="agent_messages")
    op.drop_index("ix_agent_messages_workspace_thread", table_name="agent_messages")
    op.drop_index("ix_agent_messages_workspace_task", table_name="agent_messages")
    op.drop_index("ix_agent_messages_workspace_sender", table_name="agent_messages")
    op.drop_index("ix_agent_messages_workspace_recipient_status", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_index("ix_agent_message_threads_workspace_updated", table_name="agent_message_threads")
    op.drop_index("ix_agent_message_threads_workspace_task", table_name="agent_message_threads")
    op.drop_index("ix_agent_message_threads_workspace_status", table_name="agent_message_threads")
    op.drop_table("agent_message_threads")
