"""The three PostgreSQL tables owned by knowledge."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.plugins.database import Base

from .config import EMBEDDING_DIMENSIONS


def now() -> datetime:
    """Use aware timestamps for claims and source publication."""
    return datetime.now(UTC)


class KnowledgeBaseRecord(Base):
    """Library ownership and the single active/target rebuild contract."""

    __tablename__ = "knowledge_bases"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_plugin: Mapped[str] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict
    )
    active_profile: Mapped[dict[str, Any]] = mapped_column(JSONB)
    target_profile: Mapped[dict[str, Any]] = mapped_column(JSONB)
    index_epoch: Mapped[UUID] = mapped_column(default=uuid4)
    index_status: Mapped[str] = mapped_column(String(20), default="ready")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now
    )


class DocumentRecord(Base):
    """Normalized source plus persistent claim and retry state."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index(
            "ix_knowledge_documents_claim", "status", "retry_at", "lease_until"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    media_type: Mapped[str] = mapped_column(String(100))
    source_path: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    index_key: Mapped[UUID] = mapped_column(default=uuid4)
    index_epoch: Mapped[UUID] = mapped_column()
    indexed_profile: Mapped[str | None] = mapped_column(String(64))
    claim_id: Mapped[UUID | None] = mapped_column()
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    attempts: Mapped[int] = mapped_column(default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now
    )


class ChunkRecord(Base):
    """One published source interval and its normalized BGE vector."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal"),
        Index(
            "ix_knowledge_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column()
    text: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column()
    end_offset: Mapped[int] = mapped_column()
    token_count: Mapped[int] = mapped_column()
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    index_key: Mapped[UUID] = mapped_column()
