"""add run project file staging and output harvesting

Revision ID: 0064_run_project_file_io
Revises: 0063_project_run_snapshots
Create Date: 2026-09-09 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064_run_project_file_io"
down_revision: str | None = "0063_project_run_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "artifacts",
        sa.Column("workspace_project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "artifacts",
        sa.Column("workspace_project_output_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("artifacts", sa.Column("project_path", sa.String(length=512), nullable=True))
    op.create_foreign_key(
        "fk_artifacts_workspace_project_id_workspace_projects",
        "artifacts",
        "workspace_projects",
        ["workspace_project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_artifacts_workspace_project_output_id_workspace_project_outputs",
        "artifacts",
        "workspace_project_outputs",
        ["workspace_project_output_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_artifacts_workspace_project_output",
        "artifacts",
        ["workspace_id", "workspace_project_output_id"],
    )
    op.create_unique_constraint(
        "uq_artifacts_run_project_output",
        "artifacts",
        ["agent_run_id", "workspace_project_output_id"],
    )

    op.create_table(
        "agent_run_project_io_states",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_runtime_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root_path", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("staged_file_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("staged_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("harvested_output_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("harvested_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("staged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("harvested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status in ('pending', 'staged', 'harvesting', 'harvested', 'failed')",
            name=op.f("ck_agent_run_project_io_states_status_valid"),
        ),
        sa.CheckConstraint(
            "staged_file_count >= 0 and staged_bytes >= 0 and "
            "harvested_output_count >= 0 and harvested_bytes >= 0",
            name=op.f("ck_agent_run_project_io_states_counts_non_negative"),
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_snapshot_id"], ["agent_run_project_snapshots.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_runtime_id"], ["workspace_runtimes.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_run_project_io_states")),
        sa.UniqueConstraint("agent_run_id", name="uq_agent_run_project_io_states_run"),
        sa.UniqueConstraint(
            "project_snapshot_id", name="uq_agent_run_project_io_states_snapshot"
        ),
    )
    op.create_index(
        "ix_agent_run_project_io_states_workspace_status",
        "agent_run_project_io_states",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_run_project_io_states_workspace_status",
        table_name="agent_run_project_io_states",
    )
    op.drop_table("agent_run_project_io_states")
    op.drop_constraint("uq_artifacts_run_project_output", "artifacts", type_="unique")
    op.drop_index("ix_artifacts_workspace_project_output", table_name="artifacts")
    op.drop_constraint(
        "fk_artifacts_workspace_project_output_id_workspace_project_outputs",
        "artifacts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_artifacts_workspace_project_id_workspace_projects",
        "artifacts",
        type_="foreignkey",
    )
    op.drop_column("artifacts", "project_path")
    op.drop_column("artifacts", "workspace_project_output_id")
    op.drop_column("artifacts", "workspace_project_id")
