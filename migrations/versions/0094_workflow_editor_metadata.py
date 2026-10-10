"""Initialize framework-neutral workflow editor metadata.

Revision ID: 0094_workflow_editor_metadata
Revises: 0093_mcp_discovery_metadata
"""

from alembic import op

revision = "0094_workflow_editor_metadata"
down_revision = "0093_mcp_discovery_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("orchestration_definitions", "orchestration_revisions"):
        op.execute(
            f"UPDATE {table} SET definition = definition || "
            '\'{"editor": {"positions": {}, "viewport": {"x": 0, "y": 0}, '
            '"zoom": 1}}\'::jsonb'
        )


def downgrade() -> None:
    for table in ("orchestration_definitions", "orchestration_revisions"):
        op.execute(f"UPDATE {table} SET definition = definition - 'editor'")
