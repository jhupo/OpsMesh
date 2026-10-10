"""create self hosted runtime

Revision ID: 0009_self_hosted_runtime
Revises: 0008_capabilities_mcp
Create Date: 2026-05-17 17:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_self_hosted_runtime"
down_revision: str | None = "0008_capabilities_mcp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_workspace_runtimes_workspace_provider",
        "workspace_runtimes",
        ["workspace_id", "runtime_provider", "status"],
    )
    op.create_table(
        "runtime_enrollment_tokens",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_enrollment_tokens")),
        sa.UniqueConstraint("token_hash", name="uq_runtime_enrollment_tokens_hash"),
    )
    op.create_index(
        "ix_runtime_enrollment_tokens_workspace_status",
        "runtime_enrollment_tokens",
        ["workspace_id", "status"],
    )

    op.create_table(
        "runtime_credentials",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_runtime_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_runtime_id"], ["workspace_runtimes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_credentials")),
        sa.UniqueConstraint("token_hash", name="uq_runtime_credentials_hash"),
    )
    op.create_index(
        "ix_runtime_credentials_workspace_status",
        "runtime_credentials",
        ["workspace_id", "status"],
    )

    op.create_table(
        "self_hosted_workers",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_runtime_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("machine_id", sa.String(length=160), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_runtime_id"], ["workspace_runtimes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_self_hosted_workers")),
        sa.UniqueConstraint("workspace_runtime_id", name="uq_self_hosted_workers_runtime"),
    )
    op.create_index(
        "ix_self_hosted_workers_workspace_status",
        "self_hosted_workers",
        ["workspace_id", "status"],
    )

    op.create_table(
        "self_hosted_job_claims",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["worker_id"], ["self_hosted_workers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_self_hosted_job_claims")),
        sa.UniqueConstraint("agent_run_id", name="uq_self_hosted_job_claims_run"),
    )
    op.create_index(
        "ix_self_hosted_job_claims_workspace_status",
        "self_hosted_job_claims",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_self_hosted_job_claims_workspace_worker",
        "self_hosted_job_claims",
        ["workspace_id", "worker_id"],
    )

    op.create_table(
        "local_file_references",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_runtime_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("label", sa.String(length=240), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_runtime_id"], ["workspace_runtimes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_local_file_references")),
    )
    op.create_index(
        "ix_local_file_references_workspace_runtime",
        "local_file_references",
        ["workspace_id", "workspace_runtime_id"],
    )
    op.create_index(
        "ix_local_file_references_workspace_task",
        "local_file_references",
        ["workspace_id", "task_id"],
    )

    op.create_table(
        "self_hosted_artifact_uploads",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("filename", sa.String(length=260), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["worker_id"], ["self_hosted_workers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_self_hosted_artifact_uploads")),
    )
    op.create_index(
        "ix_self_hosted_artifact_uploads_workspace_run",
        "self_hosted_artifact_uploads",
        ["workspace_id", "agent_run_id"],
    )
    op.create_index(
        "ix_self_hosted_artifact_uploads_workspace_status",
        "self_hosted_artifact_uploads",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_self_hosted_artifact_uploads_workspace_status", table_name="self_hosted_artifact_uploads")
    op.drop_index("ix_self_hosted_artifact_uploads_workspace_run", table_name="self_hosted_artifact_uploads")
    op.drop_table("self_hosted_artifact_uploads")
    op.drop_index("ix_local_file_references_workspace_task", table_name="local_file_references")
    op.drop_index("ix_local_file_references_workspace_runtime", table_name="local_file_references")
    op.drop_table("local_file_references")
    op.drop_index("ix_self_hosted_job_claims_workspace_worker", table_name="self_hosted_job_claims")
    op.drop_index("ix_self_hosted_job_claims_workspace_status", table_name="self_hosted_job_claims")
    op.drop_table("self_hosted_job_claims")
    op.drop_index("ix_self_hosted_workers_workspace_status", table_name="self_hosted_workers")
    op.drop_table("self_hosted_workers")
    op.drop_index("ix_runtime_credentials_workspace_status", table_name="runtime_credentials")
    op.drop_table("runtime_credentials")
    op.drop_index(
        "ix_runtime_enrollment_tokens_workspace_status",
        table_name="runtime_enrollment_tokens",
    )
    op.drop_table("runtime_enrollment_tokens")
    op.drop_index("ix_workspace_runtimes_workspace_provider", table_name="workspace_runtimes")
