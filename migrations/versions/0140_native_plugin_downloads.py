"""Replace the retired download lease scanner with native execution identities."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0140_native_plugin_downloads"
down_revision = "0139_restore_drill_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plugin_downloads", sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "plugin_downloads",
        sa.Column("actor_token_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute("UPDATE plugin_downloads SET execution_id = gen_random_uuid()")
    op.alter_column("plugin_downloads", "execution_id", nullable=False)
    # Retired in-flight requests require an explicit administrator retry. Migration
    # cannot recreate an execution whose former process result is unknown.
    op.execute(
        "UPDATE plugin_downloads SET status = 'failed', "
        "error_code = 'retired_execution_requires_retry' WHERE status IN ('queued', 'fetching')"
    )
    op.drop_index("ix_plugin_downloads_due", table_name="plugin_downloads")
    op.drop_column("plugin_downloads", "lease_token")
    op.drop_column("plugin_downloads", "lease_until")
    op.create_index(
        "ix_plugin_downloads_workspace_status",
        "plugin_downloads",
        ["workspace_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_plugin_downloads_workspace_status", table_name="plugin_downloads")
    op.add_column(
        "plugin_downloads", sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("plugin_downloads", sa.Column("lease_until", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_plugin_downloads_due", "plugin_downloads", ["status", "lease_until", "created_at"]
    )
    op.drop_column("plugin_downloads", "execution_id")
    op.drop_column("plugin_downloads", "actor_token_id")
