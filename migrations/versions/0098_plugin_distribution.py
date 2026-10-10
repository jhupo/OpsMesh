"""Workspace-approved catalogs and recoverable, data-only plugin downloads."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0098_plugin_distribution"
down_revision = "0097_message_collaboration"
branch_labels = None
depends_on = None


def _identity() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "plugin_sources",
        *_identity(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("allowed_hosts", JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("synced_generation", sa.Integer(), nullable=False),
        sa.UniqueConstraint("workspace_id", "name", name="uq_plugin_source_name"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_plugin_source_scope"),
        sa.CheckConstraint("generation > 0", name="generation"),
    )
    op.create_table(
        "plugin_candidates",
        *_identity(),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_key", sa.String(120), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("publisher_key_id", sa.String(120), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("withdrawn", sa.Boolean(), nullable=False),
        sa.Column("verified_release", JSONB(), nullable=True),
        sa.UniqueConstraint(
            "source_id", "plugin_key", "version", name="uq_plugin_candidate_version"
        ),
        sa.UniqueConstraint("workspace_id", "source_id", "id", name="uq_plugin_candidate_scope"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["plugin_sources.workspace_id", "plugin_sources.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "plugin_downloads",
        *_identity(),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(120), nullable=False),
        sa.Column("source_generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.UniqueConstraint("workspace_id", "request_key", name="uq_plugin_download_request"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["plugin_sources.workspace_id", "plugin_sources.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_id", "candidate_id"],
            [
                "plugin_candidates.workspace_id",
                "plugin_candidates.source_id",
                "plugin_candidates.id",
            ],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'fetching', 'succeeded', 'failed')", name="status"
        ),
    )
    op.create_index(
        "ix_plugin_downloads_due", "plugin_downloads", ["status", "lease_until", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_plugin_downloads_due", table_name="plugin_downloads")
    op.drop_table("plugin_downloads")
    op.drop_table("plugin_candidates")
    op.drop_table("plugin_sources")
