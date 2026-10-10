"""add provider default and memory fts indexes

Revision ID: 0038_provider_default_memory_fts
Revises: 0037_worker_lease_heartbeat
Create Date: 2026-06-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_provider_default_memory_fts"
down_revision: str | None = "0037_worker_lease_heartbeat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    op.execute(
        """
        WITH ranked_defaults AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY workspace_id
                    ORDER BY updated_at DESC, created_at DESC, id DESC
                ) AS default_rank
            FROM model_provider_credentials
            WHERE is_default = TRUE AND status = 'active'
        )
        UPDATE model_provider_credentials
        SET is_default = FALSE
        WHERE id IN (
            SELECT id
            FROM ranked_defaults
            WHERE default_rank > 1
        )
        """
    )
    op.create_index(
        "uq_model_provider_credentials_active_default",
        "model_provider_credentials",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("is_default IS TRUE AND status = 'active'"),
        sqlite_where=sa.text("is_default = 1 AND status = 'active'"),
    )

    if dialect_name == "postgresql":
        op.execute(
            """
            CREATE INDEX ix_workspace_memory_entries_fts_simple_active
            ON workspace_memory_entries
            USING gin (to_tsvector('simple', title || ' ' || content))
            WHERE status = 'active'
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "postgresql":
        op.execute(
            """
            DROP INDEX IF EXISTS ix_workspace_memory_entries_fts_simple_active
            """
        )

    op.drop_index(
        "uq_model_provider_credentials_active_default",
        table_name="model_provider_credentials",
    )
