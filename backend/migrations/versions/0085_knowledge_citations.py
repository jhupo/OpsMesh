"""Add source spans for materialized knowledge chunks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0085_knowledge_citations"
down_revision = "0084_knowledge_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_citations",
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
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("locator", sa.String(length=2048), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("quote", sa.String(length=2000), nullable=False),
        sa.Column("quote_sha256", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_id"],
            ["knowledge_source_ingestions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["memory_entry_id"],
            ["workspace_memory_entries.id"],
            ondelete="CASCADE",
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
            "ingestion_id",
            "chunk_index",
            name="uq_knowledge_citations_ingestion_chunk",
        ),
        sa.CheckConstraint(
            "source_version >= 1",
            name="knowledge_citation_version_positive",
        ),
        sa.CheckConstraint(
            "chunk_index >= 0",
            name="knowledge_citation_chunk_non_negative",
        ),
        sa.CheckConstraint(
            "start_offset >= 0 and end_offset > start_offset",
            name="knowledge_citation_offsets_valid",
        ),
    )
    op.create_index(
        "ix_knowledge_citations_workspace_source",
        "knowledge_citations",
        ["workspace_id", "source_id"],
    )
    op.create_index(
        "ix_knowledge_citations_ingestion",
        "knowledge_citations",
        ["ingestion_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_citations_ingestion", table_name="knowledge_citations")
    op.drop_index(
        "ix_knowledge_citations_workspace_source",
        table_name="knowledge_citations",
    )
    op.drop_table("knowledge_citations")

