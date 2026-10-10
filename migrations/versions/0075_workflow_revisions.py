"""Published workflow revisions and canonical node definitions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0075_workflow_revisions"
down_revision = "0074_orchestration_definitions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        UPDATE orchestration_definitions SET definition = jsonb_set(definition, '{nodes}',
            (SELECT jsonb_agg((node - 'node_id' - 'mcp_tools' - 'condition') ||
                jsonb_build_object('package_id', node->'node_id',
                    'required_mcp_tools', COALESCE(node->'mcp_tools', '[]'::jsonb)) ||
                CASE WHEN node->'condition' IS NOT NULL AND node->'condition' <> '{}'::jsonb
                     THEN jsonb_build_object('condition', node->'condition') ELSE '{}'::jsonb END)
             FROM jsonb_array_elements(definition->'nodes') AS node))
    """)
    )
    op.create_table(
        "orchestration_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestration_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("definition_id", "version", name="uq_orchestration_revision_version"),
    )
    op.create_index(
        "ix_orchestration_revisions_workspace",
        "orchestration_revisions",
        ["workspace_id", "definition_id"],
    )
    op.execute(
        sa.text("""
        INSERT INTO orchestration_revisions (id, workspace_id, definition_id, version, name, definition)
        SELECT id, workspace_id, id, version, name, definition
        FROM orchestration_definitions WHERE status = 'published'
    """)
    )
    op.execute(
        sa.text("""
        UPDATE task_steps SET status = 'skipped'
        WHERE status = 'cancelled' AND dependencies->>'condition_result' = 'false'
    """)
    )


def downgrade() -> None:
    op.execute(sa.text("UPDATE task_steps SET status = 'cancelled' WHERE status = 'skipped'"))
    op.drop_table("orchestration_revisions")
    op.execute(
        sa.text("""
        UPDATE orchestration_definitions SET definition = jsonb_set(definition, '{nodes}',
            (SELECT jsonb_agg((node - 'package_id' - 'required_mcp_tools' - 'join_policy') ||
                jsonb_build_object('node_id', node->'package_id',
                    'mcp_tools', COALESCE(node->'required_mcp_tools', '[]'::jsonb)))
             FROM jsonb_array_elements(definition->'nodes') AS node))
    """)
    )
