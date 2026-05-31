"""create workspace health snapshots

Revision ID: 0037_workspace_health_snapshots
Revises: 0036_create_runtime_leases
Create Date: 2026-05-31 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_workspace_health_snapshots"
down_revision: str | None = "0036_create_runtime_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_health_snapshots",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "recommended_actions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("trend_basis", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_health_snapshots")),
    )
    op.create_index(
        "ix_workspace_health_snapshots_workspace_created",
        "workspace_health_snapshots",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_workspace_health_snapshots_workspace_status",
        "workspace_health_snapshots",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_health_snapshots_workspace_status",
        table_name="workspace_health_snapshots",
    )
    op.drop_index(
        "ix_workspace_health_snapshots_workspace_created",
        table_name="workspace_health_snapshots",
    )
    op.drop_table("workspace_health_snapshots")
