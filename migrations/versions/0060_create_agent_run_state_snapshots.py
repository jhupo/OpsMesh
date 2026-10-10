"""create agent run state snapshots

Revision ID: 0060_agent_run_state_snapshots
Revises: 0059_pending_tool_invocations
Create Date: 2026-09-08 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060_agent_run_state_snapshots"
down_revision: str | None = "0059_pending_tool_invocations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_run_state_snapshots",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("sdk_version", sa.String(length=40), nullable=True),
        sa.Column("schema_version", sa.String(length=40), nullable=True),
        sa.Column("encrypted_state", sa.Text(), nullable=False),
        sa.Column("state_fingerprint", sa.String(length=80), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=120), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="paused", nullable=False),
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
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_run_state_snapshots")),
        sa.UniqueConstraint(
            "agent_run_id",
            name="uq_agent_run_state_snapshots_run",
        ),
    )
    op.create_index(
        "ix_agent_run_state_snapshots_workspace_status",
        "agent_run_state_snapshots",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_run_state_snapshots_workspace_status",
        table_name="agent_run_state_snapshots",
    )
    op.drop_table("agent_run_state_snapshots")
