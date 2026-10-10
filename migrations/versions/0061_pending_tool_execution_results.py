"""add pending tool execution results

Revision ID: 0061_pending_tool_results
Revises: 0060_agent_run_state_snapshots
Create Date: 2026-09-08 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061_pending_tool_results"
down_revision: str | None = "0060_agent_run_state_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pending_tool_invocations", sa.Column("encrypted_result", sa.Text()))
    op.add_column(
        "pending_tool_invocations",
        sa.Column("result_fingerprint", sa.String(length=80)),
    )
    op.add_column(
        "pending_tool_invocations",
        sa.Column("result_encryption_key_id", sa.String(length=120)),
    )
    op.add_column(
        "pending_tool_invocations",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "pending_tool_invocations",
        sa.Column("execution_started_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "pending_tool_invocations",
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("pending_tool_invocations", "completed_at")
    op.drop_column("pending_tool_invocations", "execution_started_at")
    op.drop_column("pending_tool_invocations", "attempt_count")
    op.drop_column("pending_tool_invocations", "result_encryption_key_id")
    op.drop_column("pending_tool_invocations", "result_fingerprint")
    op.drop_column("pending_tool_invocations", "encrypted_result")
