"""Retire unused platform model fallback configuration with reversible preservation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0137_retired_model_fallback"
down_revision = "0136_agent_tool_allowlist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration-only facts preserve the exact retired value for downgrade. They
    # are never read by execution, APIs, or model-provider resolution.
    op.create_table(
        "retired_model_fallback_settings",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("value", postgresql.JSONB(), nullable=False),
    )
    op.execute(
        "INSERT INTO retired_model_fallback_settings (workspace_id, value) "
        "SELECT id, settings -> 'model_provider_fallback' FROM workspaces "
        "WHERE settings ? 'model_provider_fallback'"
    )
    op.execute(
        "UPDATE workspaces SET settings = settings - 'model_provider_fallback' "
        "WHERE settings ? 'model_provider_fallback'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE workspaces SET settings = workspaces.settings || "
        "jsonb_build_object('model_provider_fallback', retired.value) "
        "FROM retired_model_fallback_settings AS retired "
        "WHERE workspaces.id = retired.workspace_id"
    )
    op.drop_table("retired_model_fallback_settings")
