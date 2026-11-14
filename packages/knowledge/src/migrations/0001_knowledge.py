"""Create owned libraries, resumable document indexing, and vector chunks."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "knowledge_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the independent schema after deployment installs pgvector."""
    # Deployment owns the privileges needed to install extensions.
    connection = op.get_bind()
    if (
        connection.scalar(
            sa.text(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            )
        )
        is None
    ):
        raise RuntimeError(
            "Install PostgreSQL's vector extension before knowledge migrations"
        )
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_plugin", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("active_profile", postgresql.JSONB(), nullable=False),
        sa.Column("target_profile", postgresql.JSONB(), nullable=False),
        sa.Column("index_epoch", sa.Uuid(), nullable=False),
        sa.Column("index_status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_knowledge_bases_owner_plugin", "knowledge_bases", ["owner_plugin"]
    )
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "knowledge_base_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("index_key", sa.Uuid(), nullable=False),
        sa.Column("index_epoch", sa.Uuid(), nullable=False),
        sa.Column("indexed_profile", sa.String(64)),
        sa.Column("claim_id", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_knowledge_documents_knowledge_base_id",
        "knowledge_documents",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_knowledge_documents_claim",
        "knowledge_documents",
        ["status", "retry_at", "lease_until"],
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(512), nullable=False),
        sa.Column("index_key", sa.Uuid(), nullable=False),
        sa.UniqueConstraint(
            "document_id", "ordinal", name="uq_knowledge_chunks_document_id"
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"]
    )
    op.create_index(
        "ix_knowledge_chunks_embedding",
        "knowledge_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Remove only the tables owned by knowledge."""
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
    op.drop_table("knowledge_bases")
