"""create self hosted mcp jobs

Revision ID: 0032_self_hosted_mcp_jobs
Revises: 0031_artifact_work_package_metadata
Create Date: 2026-05-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_self_hosted_mcp_jobs"
down_revision: str | None = "0031_artifact_work_package_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "self_hosted_mcp_jobs",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_runtime_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mcp_server_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(length=160), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
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
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mcp_server_id"], ["mcp_servers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["worker_id"], ["self_hosted_workers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_runtime_id"],
            ["workspace_runtimes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_self_hosted_mcp_jobs")),
    )
    op.create_index(
        "ix_self_hosted_mcp_jobs_agent_run",
        "self_hosted_mcp_jobs",
        ["agent_run_id"],
    )
    op.create_index(
        "ix_self_hosted_mcp_jobs_workspace_status",
        "self_hosted_mcp_jobs",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_self_hosted_mcp_jobs_workspace_worker",
        "self_hosted_mcp_jobs",
        ["workspace_id", "worker_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_self_hosted_mcp_jobs_workspace_worker", table_name="self_hosted_mcp_jobs")
    op.drop_index("ix_self_hosted_mcp_jobs_workspace_status", table_name="self_hosted_mcp_jobs")
    op.drop_index("ix_self_hosted_mcp_jobs_agent_run", table_name="self_hosted_mcp_jobs")
    op.drop_table("self_hosted_mcp_jobs")
