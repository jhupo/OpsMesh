"""create workspace quotas

Revision ID: 0028_create_workspace_quotas
Revises: 0027_platform_policies
Create Date: 2026-05-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_create_workspace_quotas"
down_revision: str | None = "0027_platform_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_quotas",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("quota_key", sa.String(length=80), nullable=False),
        sa.Column("limit_value", sa.Integer(), nullable=False),
        sa.Column("reserved_value", sa.Integer(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "quota_key", name="uq_workspace_quotas_key"),
    )
    op.create_index(
        "ix_workspace_quotas_workspace_status",
        "workspace_quotas",
        ["workspace_id", "status"],
        unique=False,
    )
    op.create_table(
        "workspace_reservations",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("task_step_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("reservation_key", sa.String(length=180), nullable=False),
        sa.Column("resource_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_step_id"], ["task_steps.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "reservation_key",
            name="uq_workspace_reservations_key",
        ),
    )
    op.create_index(
        "ix_workspace_reservations_workspace_status",
        "workspace_reservations",
        ["workspace_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_reservations_workspace_status",
        table_name="workspace_reservations",
    )
    op.drop_table("workspace_reservations")
    op.drop_index("ix_workspace_quotas_workspace_status", table_name="workspace_quotas")
    op.drop_table("workspace_quotas")
