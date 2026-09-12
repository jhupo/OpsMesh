from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.app.workspaces.models import Workspace


class WorkspaceMemoryEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_memory_entries"
    __table_args__ = (
        Index("ix_workspace_memory_entries_workspace_status", "workspace_id", "status"),
        Index("ix_workspace_memory_entries_workspace_type", "workspace_id", "entry_type"),
        Index("ix_workspace_memory_entries_workspace_scope", "workspace_id", "visibility_scope"),
        Index("ix_workspace_memory_entries_workspace_source", "workspace_id", "source_type"),
        Index(
            "ix_workspace_memory_entries_embedding_hnsw", "embedding",
            postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("status = 'active' AND embedding_status = 'ready'"),
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_workspace_memory_entries_fts_simple_active",
            text("to_tsvector('simple'::regconfig, (title::text || ' '::text) || content::text)"),
            postgresql_using="gin", postgresql_where=text("status = 'active'"),
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_workspace_memory_entries_full_text_gin",
            text("to_tsvector('simple'::regconfig, (title::text || ' '::text) || content::text)"),
            postgresql_using="gin",
            postgresql_where=text("status = 'active' AND memory_layer IN ('episodic', 'semantic')"),
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_workspace_memory_entries_workspace_layer_scope",
            "workspace_id",
            "memory_layer",
            "scope_type",
            "scope_id",
            "status",
        ),
        UniqueConstraint(
            "workspace_id",
            "memory_layer",
            "scope_type",
            "scope_id",
            "memory_key",
            name="uq_workspace_memory_layer_scope_key",
        ),
        CheckConstraint(
            "memory_layer IN ('working', 'episodic', 'semantic')",
            name="ck_workspace_memory_layer",
        ),
        CheckConstraint(
            "scope_type IN ('run', 'session', 'task', 'agent', 'team', 'workspace')",
            name="ck_workspace_memory_scope_type",
        ),
        CheckConstraint(
            "embedding_status IN "
            "('not_applicable', 'pending', 'queued', 'processing', 'ready', 'failed')",
            name="ck_workspace_memory_embedding_status",
        ),
        CheckConstraint(
            "embedding_generation >= 0",
            name="ck_workspace_memory_embedding_generation",
        ),
        CheckConstraint(
            "embedding_attempts >= 0",
            name="ck_workspace_memory_embedding_attempts",
        ),
        CheckConstraint(
            "embedding_status != 'ready' OR (embedding IS NOT NULL "
            "AND embedding_model IS NOT NULL "
            "AND embedding_content_fingerprint IS NOT NULL "
            "AND embedded_at IS NOT NULL)",
            name="ck_workspace_memory_ready_embedding_complete",
        ),
        CheckConstraint(
            "(embedding_status = 'processing' AND embedding_processing_started_at IS NOT NULL) "
            "OR (embedding_status != 'processing' AND embedding_processing_started_at IS NULL)",
            name="ck_workspace_memory_processing_started_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    memory_layer: Mapped[str] = mapped_column(String(32), nullable=False, default="semantic")
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace")
    scope_id: Mapped[str] = mapped_column(String(240), nullable=False)
    memory_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    entry_type: Mapped[str] = mapped_column(String(80), nullable=False, default="note")
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False, default="")
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    visibility_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace")
    importance: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    memory_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(1_536), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    embedding_content_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    embedding_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="not_applicable",
    )
    embedding_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding_processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def invalidate_embedding(self, *, status: str, advance_generation: bool = True) -> None:
        if status not in {"not_applicable", "pending"}:
            raise ValueError("Invalid memory embedding reset status")
        self.embedding = None
        self.embedding_model = None
        self.embedding_content_fingerprint = None
        self.embedding_status = status
        if advance_generation:
            self.embedding_generation += 1
        self.embedding_attempts = 0
        self.embedding_last_error_code = None
        self.embedding_processing_started_at = None
        self.embedded_at = None


class WorkspaceMemoryVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "workspace_memory_versions"
    __table_args__ = (
        UniqueConstraint(
            "memory_entry_id",
            "revision",
            name="uq_workspace_memory_versions_entry_revision",
        ),
        Index(
            "ix_workspace_memory_versions_workspace_entry",
            "workspace_id",
            "memory_entry_id",
            "revision",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    memory_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_memory_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    changed_by_agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    changed_by_agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    change_reason: Mapped[str | None] = mapped_column(String(1_000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class WorkspaceMemoryConfiguration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_memory_configurations"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_workspace_memory_configurations_workspace"),
        CheckConstraint(
            "embedding_dimensions = 1536",
            name="ck_workspace_memory_configuration_dimensions",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_workspace_memory_configuration_version",
        ),
        CheckConstraint(
            "(embedding_enabled = FALSE AND embedding_credential_id IS NULL) OR "
            "(embedding_enabled = TRUE AND embedding_credential_id IS NOT NULL)",
            name="ck_workspace_memory_configuration_credential",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    embedding_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    embedding_credential_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_provider_credentials.id", ondelete="RESTRICT"),
        nullable=True,
    )
    embedding_model: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        default="text-embedding-3-small",
    )
    embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=1_536)
    retrieval_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    lifecycle_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    workspace: Mapped["Workspace"] = relationship()


class WorkspaceMemoryRetrievalEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "workspace_memory_retrieval_events"
    __table_args__ = (
        Index(
            "ix_workspace_memory_retrieval_events_workspace_created",
            "workspace_id",
            "created_at",
        ),
        CheckConstraint("requested_limit > 0", name="ck_memory_retrieval_requested_limit"),
        CheckConstraint("candidate_count >= 0", name="ck_memory_retrieval_candidate_count"),
        CheckConstraint(
            "deduplicated_count >= 0",
            name="ck_memory_retrieval_deduplicated_count",
        ),
        Index(
            "ix_workspace_memory_retrieval_events_run_created",
            "agent_run_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    query_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    query_terms: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    scope_filters: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    ranking_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    backend_evidence: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    selected: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deduplicated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class WorkspaceMemoryLifecycleEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "workspace_memory_lifecycle_events"
    __table_args__ = (
        Index(
            "ix_workspace_memory_lifecycle_events_workspace_created",
            "workspace_id",
            "created_at",
        ),
        CheckConstraint(
            "action IN ('archived', 'promoted')",
            name="ck_memory_lifecycle_event_action",
        ),
        CheckConstraint("policy_version >= 1", name="ck_memory_lifecycle_policy_version"),
        Index(
            "ix_workspace_memory_lifecycle_events_entry_created",
            "memory_entry_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    memory_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_memory_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    before: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    after: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class WorkspaceMemoryEmbeddingEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "workspace_memory_embedding_events"
    __table_args__ = (
        Index(
            "ix_workspace_memory_embedding_events_workspace_created",
            "workspace_id",
            "created_at",
        ),
        CheckConstraint(
            "status IN ('completed', 'failed', 'recovered')",
            name="ck_memory_embedding_event_status",
        ),
        CheckConstraint(
            "embedding_generation >= 0",
            name="ck_memory_embedding_event_generation",
        ),
        CheckConstraint("dimensions > 0", name="ck_memory_embedding_event_dimensions"),
        CheckConstraint("input_tokens >= 0", name="ck_memory_embedding_event_input_tokens"),
        Index(
            "ix_workspace_memory_embedding_events_entry_created",
            "memory_entry_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    memory_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_memory_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    credential_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_provider_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    configuration_version: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
