"""Database operational configuration,  variable vector dimensions."""

import json
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects.postgresql import JSONB

revision = "0113_database_configuration"
down_revision = "0112_configured_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    op.add_column(
        "model_provider_credentials",
        sa.Column("model_capabilities", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "orchestration_definition_id",
            sa.Uuid(),
            sa.ForeignKey("orchestration_definitions.id", name="fk_conversations_workflow"),
            nullable=True,
        ),
    )
    op.add_column("conversations", sa.Column("orchestration_version", sa.Integer(), nullable=True))
    data = json.loads(
        (Path(__file__).parents[1] / "data/0113_configuration.json").read_text(encoding="utf-8")
    )
    for key, value in data.items():
        connection.execute(
            sa.text(
                "INSERT INTO platform_policies (id, policy_key, status, value, description) "
                "VALUES (:id, :key, 'active', CAST(:value AS jsonb), 'Database configuration')"
            ),
            {"id": uuid4(), "key": key, "value": json.dumps(value)},
        )
    op.drop_index(
        "ix_workspace_memory_entries_embedding_hnsw", table_name="workspace_memory_entries"
    )
    op.alter_column(
        "workspace_memory_entries", "embedding", type_=VECTOR(), existing_type=VECTOR(1536)
    )
    op.drop_constraint(
        "ck_workspace_memory_configuration_dimensions",
        "workspace_memory_configurations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_workspace_memory_configuration_dimensions",
        "workspace_memory_configurations",
        "embedding_dimensions >= 1 AND embedding_dimensions <= 16000",
    )
    # Data migration only. Runtime does not contain a legacy restart-policy fallback.
    connection.execute(
        sa.text("""
        UPDATE mcp_servers SET connection = connection || jsonb_build_object('restart_policy',
            jsonb_build_object('max_restarts', 5, 'stable_after_seconds', 300,
                'initial_backoff_seconds', 2, 'max_backoff_seconds', 60))
        WHERE connection->>'runtime' = 'managed'
    """)
    )


def downgrade() -> None:
    op.drop_column("conversations", "orchestration_version")
    op.drop_constraint("fk_conversations_workflow", "conversations", type_="foreignkey")
    op.drop_column("conversations", "orchestration_definition_id")
    connection = op.get_bind()
    incompatible = connection.scalar(
        sa.text(
            "SELECT count(*) FROM workspace_memory_configurations WHERE embedding_dimensions != 1536"
        )
    )
    if incompatible:
        raise RuntimeError(
            "Restore 1536-dimensional memory configurations and rebuild embeddings before downgrade"
        )
    op.alter_column(
        "workspace_memory_entries", "embedding", type_=VECTOR(1536), existing_type=VECTOR()
    )
    op.drop_constraint(
        "ck_workspace_memory_configuration_dimensions",
        "workspace_memory_configurations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_workspace_memory_configuration_dimensions",
        "workspace_memory_configurations",
        "embedding_dimensions = 1536",
    )
    op.create_index(
        "ix_workspace_memory_entries_embedding_hnsw",
        "workspace_memory_entries",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("status = 'active' AND embedding_status = 'ready'"),
    )
    op.drop_column("model_provider_credentials", "model_capabilities")
    data = json.loads(
        (Path(__file__).parents[1] / "data/0113_configuration.json").read_text(encoding="utf-8")
    )
    for key in data:
        connection.execute(
            sa.text("DELETE FROM platform_policies WHERE policy_key=:key"), {"key": key}
        )
    connection.execute(
        sa.text(
            "UPDATE mcp_servers SET connection=connection - 'restart_policy' WHERE connection->>'runtime'='managed'"
        )
    )
