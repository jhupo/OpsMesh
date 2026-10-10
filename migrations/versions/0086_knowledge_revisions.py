"""Add immutable knowledge-source version snapshots."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0086_knowledge_revisions"
down_revision = "0085_knowledge_citations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_source_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("changed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("uri", sa.String(length=2048), nullable=True),
        sa.Column("workspace_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("config", postgresql.JSONB, nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("change_reason", sa.String(length=240), nullable=True),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"],
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
            "version",
            name="uq_knowledge_source_revisions_source_version",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="knowledge_source_revision_version_positive",
        ),
        sa.CheckConstraint(
            "source_type in ('url', 'workspace_file')",
            name="knowledge_source_revision_type_valid",
        ),
        sa.CheckConstraint(
            "status in ('active', 'paused', 'archived')",
            name="knowledge_source_revision_status_valid",
        ),
    )
    op.create_index(
        "ix_knowledge_source_revisions_workspace_source",
        "knowledge_source_revisions",
        ["workspace_id", "source_id"],
    )
    sources = sa.table(
        "knowledge_sources",
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
        sa.column("created_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("source_type", sa.String()),
        sa.column("uri", sa.String()),
        sa.column("workspace_file_id", postgresql.UUID(as_uuid=True)),
        sa.column("config", postgresql.JSONB),
        sa.column("source_fingerprint", sa.String()),
        sa.column("version", sa.Integer()),
        sa.column("status", sa.String()),
    )
    revisions = sa.table(
        "knowledge_source_revisions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
        sa.column("source_id", postgresql.UUID(as_uuid=True)),
        sa.column("changed_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.column("version", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("source_type", sa.String()),
        sa.column("uri", sa.String()),
        sa.column("workspace_file_id", postgresql.UUID(as_uuid=True)),
        sa.column("config", postgresql.JSONB),
        sa.column("source_fingerprint", sa.String()),
        sa.column("status", sa.String()),
        sa.column("change_reason", sa.String()),
    )
    bind = op.get_bind()
    for source in bind.execute(sa.select(sources)).mappings():
        bind.execute(
            revisions.insert().values(
                id=uuid4(),
                created_at=source["created_at"],
                workspace_id=source["workspace_id"],
                source_id=source["id"],
                changed_by_user_id=source["created_by_user_id"],
                version=source["version"],
                name=source["name"],
                description=source["description"],
                source_type=source["source_type"],
                uri=source["uri"],
                workspace_file_id=source["workspace_file_id"],
                config=source["config"],
                source_fingerprint=source["source_fingerprint"],
                status=source["status"],
                change_reason="migration:initial",
            )
        )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_source_revisions_workspace_source",
        table_name="knowledge_source_revisions",
    )
    op.drop_table("knowledge_source_revisions")
