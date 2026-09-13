"""Add durable knowledge-source ingestion attempts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0084_knowledge_ingestion"
down_revision = "0083_knowledge_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_source_ingestions",
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
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("byte_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["knowledge_sources.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "source_version",
            name="uq_knowledge_source_ingestions_source_version",
        ),
        sa.CheckConstraint(
            "status in ('pending', 'processing', 'succeeded', 'failed')",
            name="knowledge_source_ingestion_status_valid",
        ),
        sa.CheckConstraint(
            "source_version >= 1",
            name="knowledge_source_ingestion_version_positive",
        ),
        sa.CheckConstraint(
            "attempts >= 0 and byte_count >= 0 and chunk_count >= 0",
            name="knowledge_source_ingestion_counts_non_negative",
        ),
    )
    op.create_index(
        "ix_knowledge_source_ingestions_workspace_status",
        "knowledge_source_ingestions",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_knowledge_source_ingestions_source_created",
        "knowledge_source_ingestions",
        ["source_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_source_ingestions_source_created",
        table_name="knowledge_source_ingestions",
    )
    op.drop_index(
        "ix_knowledge_source_ingestions_workspace_status",
        table_name="knowledge_source_ingestions",
    )
    op.drop_table("knowledge_source_ingestions")
