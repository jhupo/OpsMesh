"""complete memory lifecycle

Revision ID: 0070_memory_lifecycle
Revises: 0069_semantic_memory
Create Date: 2026-09-09 00:00:00.000000
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "0070_memory_lifecycle"
down_revision: str | None = "0069_semantic_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RETRIEVAL_POLICY = {
    "full_text_weight": 1.0,
    "vector_weight": 1.0,
    "lexical_weight": 0.35,
    "reciprocal_rank_constant": 60,
    "candidate_multiplier": 4,
    "importance_weight": 0.15,
    "recency_weight": 0.1,
    "recency_half_life_days": 30,
}
_LIFECYCLE_POLICY = {
    "episodic_decay_half_life_days": 90,
    "semantic_decay_half_life_days": 365,
    "archive_expired_episodes": True,
    "semantic_archive_after_days": None,
    "auto_promote_episodes": False,
    "promotion_min_importance": 75,
    "promotion_min_access_count": 3,
}


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_postgres else sa.JSON()
    uuid_type = postgresql.UUID(as_uuid=True) if is_postgres else sa.Uuid()
    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "workspace_memory_entries",
        sa.Column("embedding", VECTOR(1_536) if is_postgres else sa.JSON(), nullable=True),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("embedding_model", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("embedding_content_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column(
            "embedding_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_applicable",
        ),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("embedding_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("embedding_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("embedding_last_error_code", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("embedding_processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_workspace_memory_embedding_status",
        "workspace_memory_entries",
        "embedding_status IN "
        "('not_applicable', 'pending', 'queued', 'processing', 'ready', 'failed')",
    )
    op.create_check_constraint(
        "ck_workspace_memory_embedding_generation",
        "workspace_memory_entries",
        "embedding_generation >= 0",
    )
    op.create_check_constraint(
        "ck_workspace_memory_embedding_attempts",
        "workspace_memory_entries",
        "embedding_attempts >= 0",
    )
    op.create_check_constraint(
        "ck_workspace_memory_ready_embedding_complete",
        "workspace_memory_entries",
        "embedding_status != 'ready' OR (embedding IS NOT NULL "
        "AND embedding_model IS NOT NULL "
        "AND embedding_content_fingerprint IS NOT NULL "
        "AND embedded_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_workspace_memory_processing_started_at",
        "workspace_memory_entries",
        "(embedding_status = 'processing' AND embedding_processing_started_at IS NOT NULL) "
        "OR (embedding_status != 'processing' AND embedding_processing_started_at IS NULL)",
    )
    op.create_table(
        "workspace_memory_configurations",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("embedding_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("embedding_credential_id", uuid_type, nullable=True),
        sa.Column(
            "embedding_model",
            sa.String(length=160),
            nullable=False,
            server_default="text-embedding-3-small",
        ),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False, server_default="1536"),
        sa.Column("retrieval_policy", json_type, nullable=False),
        sa.Column("lifecycle_policy", json_type, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by_user_id", uuid_type, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", uuid_type, nullable=False),
        sa.CheckConstraint(
            "embedding_dimensions = 1536",
            name="ck_workspace_memory_configuration_dimensions",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_workspace_memory_configuration_version",
        ),
        sa.CheckConstraint(
            "(embedding_enabled = FALSE AND embedding_credential_id IS NULL) OR "
            "(embedding_enabled = TRUE AND embedding_credential_id IS NOT NULL)",
            name="ck_workspace_memory_configuration_credential",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["embedding_credential_id"],
            ["model_provider_credentials.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            name="uq_workspace_memory_configurations_workspace",
        ),
    )
    _create_retrieval_events(uuid_type, json_type)
    _create_lifecycle_events(uuid_type, json_type)
    _create_embedding_events(uuid_type)
    workspaces = sa.table("workspaces", sa.column("id", uuid_type))
    configurations = sa.table(
        "workspace_memory_configurations",
        sa.column("id", uuid_type),
        sa.column("workspace_id", uuid_type),
        sa.column("embedding_enabled", sa.Boolean()),
        sa.column("embedding_model", sa.String()),
        sa.column("embedding_dimensions", sa.Integer()),
        sa.column("retrieval_policy", json_type),
        sa.column("lifecycle_policy", json_type),
        sa.column("version", sa.Integer()),
    )
    for workspace_id in bind.scalars(sa.select(workspaces.c.id)):
        bind.execute(
            configurations.insert().values(
                id=uuid4(),
                workspace_id=workspace_id,
                embedding_enabled=False,
                embedding_model="text-embedding-3-small",
                embedding_dimensions=1_536,
                retrieval_policy=_RETRIEVAL_POLICY,
                lifecycle_policy=_LIFECYCLE_POLICY,
                version=1,
            )
        )
    if is_postgres:
        op.execute(
            """
            CREATE INDEX ix_workspace_memory_entries_full_text_gin
            ON workspace_memory_entries
            USING gin (to_tsvector('simple', title || ' ' || content))
            WHERE status = 'active' AND memory_layer IN ('episodic', 'semantic')
            """
        )
        op.execute(
            """
            CREATE INDEX ix_workspace_memory_entries_embedding_hnsw
            ON workspace_memory_entries
            USING hnsw (embedding vector_cosine_ops)
            WHERE status = 'active' AND embedding_status = 'ready'
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_workspace_memory_entries_embedding_hnsw")
        op.execute("DROP INDEX IF EXISTS ix_workspace_memory_entries_full_text_gin")
    op.drop_table("workspace_memory_embedding_events")
    op.drop_table("workspace_memory_lifecycle_events")
    op.drop_table("workspace_memory_retrieval_events")
    op.drop_table("workspace_memory_configurations")
    op.drop_constraint(
        "ck_workspace_memory_processing_started_at",
        "workspace_memory_entries",
        type_="check",
    )
    op.drop_constraint(
        "ck_workspace_memory_ready_embedding_complete",
        "workspace_memory_entries",
        type_="check",
    )
    op.drop_constraint(
        "ck_workspace_memory_embedding_attempts",
        "workspace_memory_entries",
        type_="check",
    )
    op.drop_constraint(
        "ck_workspace_memory_embedding_generation",
        "workspace_memory_entries",
        type_="check",
    )
    op.drop_constraint(
        "ck_workspace_memory_embedding_status",
        "workspace_memory_entries",
        type_="check",
    )
    for column_name in (
        "embedded_at",
        "embedding_processing_started_at",
        "embedding_last_error_code",
        "embedding_attempts",
        "embedding_generation",
        "embedding_status",
        "embedding_content_fingerprint",
        "embedding_model",
        "embedding",
    ):
        op.drop_column("workspace_memory_entries", column_name)


def _create_retrieval_events(uuid_type: sa.types.TypeEngine, json_type: sa.types.TypeEngine) -> None:
    op.create_table(
        "workspace_memory_retrieval_events",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("agent_run_id", uuid_type, nullable=True),
        sa.Column("query_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("query_terms", json_type, nullable=False),
        sa.Column("requested_limit", sa.Integer(), nullable=False),
        sa.Column("scope_filters", json_type, nullable=False),
        sa.Column("ranking_policy", json_type, nullable=False),
        sa.Column("backend_evidence", json_type, nullable=False),
        sa.Column("selected", json_type, nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deduplicated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("id", uuid_type, nullable=False),
        sa.CheckConstraint("requested_limit > 0", name="ck_memory_retrieval_requested_limit"),
        sa.CheckConstraint("candidate_count >= 0", name="ck_memory_retrieval_candidate_count"),
        sa.CheckConstraint(
            "deduplicated_count >= 0",
            name="ck_memory_retrieval_deduplicated_count",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_memory_retrieval_events_workspace_created",
        "workspace_memory_retrieval_events",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_workspace_memory_retrieval_events_run_created",
        "workspace_memory_retrieval_events",
        ["agent_run_id", "created_at"],
    )


def _create_lifecycle_events(uuid_type: sa.types.TypeEngine, json_type: sa.types.TypeEngine) -> None:
    op.create_table(
        "workspace_memory_lifecycle_events",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("memory_entry_id", uuid_type, nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("before", json_type, nullable=False),
        sa.Column("after", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("id", uuid_type, nullable=False),
        sa.CheckConstraint(
            "action IN ('archived', 'promoted')",
            name="ck_memory_lifecycle_event_action",
        ),
        sa.CheckConstraint("policy_version >= 1", name="ck_memory_lifecycle_policy_version"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["memory_entry_id"],
            ["workspace_memory_entries.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_memory_lifecycle_events_workspace_created",
        "workspace_memory_lifecycle_events",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_workspace_memory_lifecycle_events_entry_created",
        "workspace_memory_lifecycle_events",
        ["memory_entry_id", "created_at"],
    )


def _create_embedding_events(uuid_type: sa.types.TypeEngine) -> None:
    op.create_table(
        "workspace_memory_embedding_events",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("memory_entry_id", uuid_type, nullable=False),
        sa.Column("credential_id", uuid_type, nullable=True),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("embedding_generation", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("id", uuid_type, nullable=False),
        sa.CheckConstraint(
            "status IN ('completed', 'failed', 'recovered')",
            name="ck_memory_embedding_event_status",
        ),
        sa.CheckConstraint(
            "embedding_generation >= 0",
            name="ck_memory_embedding_event_generation",
        ),
        sa.CheckConstraint("dimensions > 0", name="ck_memory_embedding_event_dimensions"),
        sa.CheckConstraint("input_tokens >= 0", name="ck_memory_embedding_event_input_tokens"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["memory_entry_id"],
            ["workspace_memory_entries.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["model_provider_credentials.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_memory_embedding_events_workspace_created",
        "workspace_memory_embedding_events",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_workspace_memory_embedding_events_entry_created",
        "workspace_memory_embedding_events",
        ["memory_entry_id", "created_at"],
    )
