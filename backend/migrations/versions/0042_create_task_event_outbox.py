"""create task event outbox

Revision ID: 0042_task_event_outbox
Revises: 0041_agent_message_mailbox
Create Date: 2026-06-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_task_event_outbox"
down_revision: str | None = "0041_agent_message_mailbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_event_outbox",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stream_id", sa.String(length=80), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status in ('pending', 'publishing', 'published', 'failed')",
            name=op.f("ck_task_event_outbox_status_valid"),
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name=op.f("ck_task_event_outbox_attempts_non_negative"),
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_event_outbox")),
        sa.UniqueConstraint("event_id", name=op.f("uq_task_event_outbox_event_id")),
    )
    op.create_index(
        "ix_task_event_outbox_status_available",
        "task_event_outbox",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_task_event_outbox_workspace_status",
        "task_event_outbox",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_task_event_outbox_workspace_task",
        "task_event_outbox",
        ["workspace_id", "task_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_event_outbox_workspace_task", table_name="task_event_outbox")
    op.drop_index("ix_task_event_outbox_workspace_status", table_name="task_event_outbox")
    op.drop_index("ix_task_event_outbox_status_available", table_name="task_event_outbox")
    op.drop_table("task_event_outbox")
