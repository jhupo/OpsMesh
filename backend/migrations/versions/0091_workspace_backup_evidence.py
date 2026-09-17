"""record workspace archive backup evidence

Revision ID: 0091_workspace_backup_evidence
Revises: 0090_mcp_execution_freshness
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0091_workspace_backup_evidence"
down_revision: str | None = "0090_mcp_execution_freshness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_export_jobs",
        sa.Column("source_release", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "workspace_export_jobs",
        sa.Column("source_schema_revision", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "workspace_export_jobs",
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workspace_export_jobs",
        sa.Column("verification_status", sa.String(length=32), nullable=False, server_default="unverified"),
    )
    op.add_column(
        "workspace_export_jobs",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workspace_export_jobs",
        sa.Column("manifest_checksum_sha256", sa.String(length=64), nullable=True),
    )
    op.alter_column("workspace_export_jobs", "verification_status", server_default=None)


def downgrade() -> None:
    op.drop_column("workspace_export_jobs", "manifest_checksum_sha256")
    op.drop_column("workspace_export_jobs", "verified_at")
    op.drop_column("workspace_export_jobs", "verification_status")
    op.drop_column("workspace_export_jobs", "retention_until")
    op.drop_column("workspace_export_jobs", "source_schema_revision")
    op.drop_column("workspace_export_jobs", "source_release")
