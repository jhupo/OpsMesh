"""create task messages

Revision ID: 0023_create_task_messages
Revises: 0022_enhance_task_steps_work_packages
Create Date: 2026-05-19 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_create_task_messages"
down_revision: str | None = "0022_enhance_task_steps_work_packages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_messages",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_step_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message_type", sa.String(length=80), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_step_id"], ["task_steps.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_messages")),
        sa.UniqueConstraint("task_id", "sequence", name="uq_task_messages_task_sequence"),
    )
    op.create_index(
        "ix_task_messages_workspace_agent",
        "task_messages",
        ["workspace_id", "agent_profile_id"],
    )
    op.create_index(
        "ix_task_messages_workspace_task",
        "task_messages",
        ["workspace_id", "task_id"],
    )
    op.create_index(
        "ix_task_messages_workspace_type",
        "task_messages",
        ["workspace_id", "message_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_messages_workspace_type", table_name="task_messages")
    op.drop_index("ix_task_messages_workspace_task", table_name="task_messages")
    op.drop_index("ix_task_messages_workspace_agent", table_name="task_messages")
    op.drop_table("task_messages")
