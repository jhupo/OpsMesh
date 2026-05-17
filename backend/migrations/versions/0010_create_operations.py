"""create operations

Revision ID: 0010_operations
Revises: 0009_self_hosted_runtime
Create Date: 2026-05-17 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_operations"
down_revision: str | None = "0009_self_hosted_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("worker_id", sa.String(length=160), nullable=False),
        sa.Column("worker_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("queue_name", sa.String(length=120), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_heartbeats")),
    )
    op.create_index("ix_worker_heartbeats_worker", "worker_heartbeats", ["worker_id"])
    op.create_index(
        "ix_worker_heartbeats_workspace_status",
        "worker_heartbeats",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_heartbeats_workspace_status", table_name="worker_heartbeats")
    op.drop_index("ix_worker_heartbeats_worker", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
