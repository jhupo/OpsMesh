"""create pending tool invocations

Revision ID: 0059_pending_tool_invocations
Revises: 0058_team_capability_policy
Create Date: 2026-09-08 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0059_pending_tool_invocations"
down_revision: str | None = "0058_team_capability_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_tool_invocations",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("tool_kind", sa.String(length=32), nullable=False),
        sa.Column("encrypted_arguments", sa.Text(), nullable=False),
        sa.Column("arguments_fingerprint", sa.String(length=80), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=120), nullable=False),
        sa.Column(
            "policy_decision",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_tool_invocations")),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_pending_tool_invocations_workspace_idempotency",
        ),
        sa.UniqueConstraint(
            "approval_id",
            name="uq_pending_tool_invocations_approval",
        ),
    )
    op.create_index(
        "ix_pending_tool_invocations_workspace_status",
        "pending_tool_invocations",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_pending_tool_invocations_workspace_run",
        "pending_tool_invocations",
        ["workspace_id", "agent_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pending_tool_invocations_workspace_run",
        table_name="pending_tool_invocations",
    )
    op.drop_index(
        "ix_pending_tool_invocations_workspace_status",
        table_name="pending_tool_invocations",
    )
    op.drop_table("pending_tool_invocations")
