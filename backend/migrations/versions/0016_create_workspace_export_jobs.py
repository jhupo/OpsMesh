"""create workspace export jobs

Revision ID: 0016_workspace_export_jobs
Revises: 0015_talent_reviews_metrics
Create Date: 2026-05-18 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_workspace_export_jobs"
down_revision: str | None = "0015_talent_reviews_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_export_jobs",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("export_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column("filename", sa.String(length=260), nullable=True),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("error", sa.String(length=1000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_export_jobs")),
    )
    op.create_index(
        "ix_workspace_export_jobs_workspace_status",
        "workspace_export_jobs",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_workspace_export_jobs_created_by",
        "workspace_export_jobs",
        ["workspace_id", "created_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_export_jobs_created_by", table_name="workspace_export_jobs")
    op.drop_index("ix_workspace_export_jobs_workspace_status", table_name="workspace_export_jobs")
    op.drop_table("workspace_export_jobs")
