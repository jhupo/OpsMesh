"""Persist workspace knowledge-source declarations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0083_knowledge_sources"
down_revision = "0082_runtime_execution_modes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("uri", sa.String(length=2048), nullable=True),
        sa.Column("workspace_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("config", postgresql.JSONB, nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_file_id"], ["workspace_files.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_knowledge_sources_workspace_name"),
        sa.CheckConstraint(
            "source_type in ('url', 'workspace_file')",
            name="knowledge_source_type_valid",
        ),
        sa.CheckConstraint(
            "status in ('active', 'paused', 'archived')",
            name="knowledge_source_status_valid",
        ),
        sa.CheckConstraint("version >= 1", name="knowledge_source_version_positive"),
    )
    op.create_index(
        "ix_knowledge_sources_workspace_status",
        "knowledge_sources",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_knowledge_sources_workspace_type",
        "knowledge_sources",
        ["workspace_id", "source_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_sources_workspace_type", table_name="knowledge_sources")
    op.drop_index("ix_knowledge_sources_workspace_status", table_name="knowledge_sources")
    op.drop_table("knowledge_sources")
