"""Remote plugin trust, releases and resource bindings.

Revision ID: 0096_remote_plugin_lifecycle
Revises: 0095_business_automations
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0096_remote_plugin_lifecycle"
down_revision = "0095_business_automations"
branch_labels = None
depends_on = None


def _identity() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "plugin_trust_keys",
        *_identity(),
        sa.Column("key_id", sa.String(120), nullable=False),
        sa.Column("plugin_key", sa.String(120), nullable=False),
        sa.Column("public_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.UniqueConstraint("workspace_id", "key_id", name="uq_plugin_trust_key"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_plugin_trust_scope"),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_plugin_trust_status"),
    )
    op.create_table(
        "plugin_installs",
        *_identity(),
        sa.Column("plugin_key", sa.String(120), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("current_version", sa.String(64), nullable=False),
        sa.UniqueConstraint("workspace_id", "plugin_key", name="uq_plugin_install_key"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_plugin_install_scope"),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'uninstalled')", name="ck_plugin_install_status"
        ),
        sa.CheckConstraint("generation > 0", name="ck_plugin_generation"),
    )
    op.create_table(
        "plugin_releases",
        *_identity(),
        sa.Column(
            "install_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "trust_key_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("package", JSONB(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("approved_permissions", JSONB(), nullable=False),
        sa.UniqueConstraint("install_id", "version", name="uq_plugin_release_version"),
        sa.UniqueConstraint("workspace_id", "install_id", "id", name="uq_plugin_release_scope"),
        sa.CheckConstraint("status IN ('available', 'retired')", name="ck_plugin_release_status"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "install_id"],
            ["plugin_installs.workspace_id", "plugin_installs.id"],
            name="fk_plugin_release_install_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "trust_key_id"],
            ["plugin_trust_keys.workspace_id", "plugin_trust_keys.id"],
            name="fk_plugin_release_trust_scope",
        ),
    )
    op.create_table(
        "plugin_bindings",
        *_identity(),
        sa.Column(
            "install_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "release_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column("capability_key", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("configuration", JSONB(), nullable=False),
        sa.UniqueConstraint("workspace_id", "kind", "resource_id", name="uq_plugin_resource_owner"),
        sa.UniqueConstraint("release_id", "capability_key", name="uq_plugin_release_capability"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "install_id", "release_id"],
            ["plugin_releases.workspace_id", "plugin_releases.install_id", "plugin_releases.id"],
            name="fk_plugin_binding_release_scope",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "kind IN ('mcp_server', 'skill', 'message_trigger', 'reply_channel')",
            name="ck_plugin_binding_kind",
        ),
    )
    op.create_index("ix_plugin_bindings_install", "plugin_bindings", ["workspace_id", "install_id"])
    # Old metadata-only installs have no executable resource; do not silently promote them.
    op.execute(
        "UPDATE workspace_marketplace_installs SET status = 'disabled' WHERE listing_type = 'plugin' AND installed_resource_id IS NULL"
    )
    op.execute("UPDATE marketplace_listings SET status = 'disabled' WHERE listing_type = 'plugin'")


def downgrade() -> None:
    op.execute(
        "UPDATE workspace_marketplace_installs SET status = 'disabled', "
        "installed_resource_id = NULL WHERE listing_type = 'plugin'"
    )
    op.execute("UPDATE marketplace_listings SET status = 'disabled' WHERE listing_type = 'plugin'")
    op.drop_table("plugin_bindings")
    op.drop_table("plugin_releases")
    op.drop_table("plugin_installs")
    op.drop_table("plugin_trust_keys")
