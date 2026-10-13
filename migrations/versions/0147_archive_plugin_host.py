"""Retire hosted plugin execution while preserving historical operational evidence."""

from alembic import op

revision = "0147_archive_plugin_host"
down_revision = "0146_channel_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (
            SELECT 1 FROM plugin_deployments
            WHERE status NOT IN ('stopped', 'failed', 'blocked')
        ) OR EXISTS (
            SELECT 1 FROM sandbox_references
            WHERE owner_kind = 'plugin' AND status <> 'deleted'
        ) THEN
            RAISE EXCEPTION 'Stop and reconcile hosted plugin processes before retiring the plugin host';
        END IF;
    END $$""")
    op.execute("""UPDATE plugin_credentials SET status = 'revoked'
        WHERE id IN (SELECT credential_id FROM plugin_deployments)""")
    # No ORM or execution path reads this archive. Keep existing foreign keys and
    # encrypted configuration so an operator can inspect the original evidence.
    op.rename_table("plugin_deployments", "archived_plugin_deployments")


def downgrade() -> None:
    op.rename_table("archived_plugin_deployments", "plugin_deployments")
    # Revoked credentials remain revoked; restoring a host never restores authority.
