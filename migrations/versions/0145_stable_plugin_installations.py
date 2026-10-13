"""Separate immutable releases from stable workspace plugin installations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0145_stable_plugin_installations"
down_revision = "0144_runtime_space_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Channel routes cannot be inferred safely from a legacy Automation or webhook.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM plugin_bindings
                       WHERE kind IN ('message_trigger', 'reply_channel')) THEN
                RAISE EXCEPTION 'Remove legacy message/reply plugin bindings before migration; '
                    'create explicit channel connections after upgrading';
            END IF;
        END $$
    """)
    op.add_column("plugin_installs", sa.Column("instance_name", sa.String(120)))
    op.add_column("plugin_installs", sa.Column("current_release_id", sa.Uuid()))
    op.add_column("plugin_installs", sa.Column("approved_permissions", JSONB()))
    op.add_column("plugin_releases", sa.Column("plugin_key", sa.String(120)))
    op.execute("""
        UPDATE plugin_releases r SET plugin_key = i.plugin_key
        FROM plugin_installs i WHERE i.workspace_id = r.workspace_id AND i.id = r.install_id
    """)
    op.execute("""
        UPDATE plugin_installs i SET instance_name = i.plugin_key,
            current_release_id = r.id, approved_permissions = r.approved_permissions,
            status = CASE WHEN i.status = 'uninstalled' THEN i.status ELSE 'disabled' END,
            generation = i.generation + 1
        FROM plugin_releases r WHERE r.workspace_id = i.workspace_id
            AND r.install_id = i.id AND r.version = i.current_version
    """)
    op.execute("UPDATE plugin_credentials SET status = 'revoked' WHERE status = 'active'")
    op.execute("""
        UPDATE workspace_marketplace_installs SET status = 'disabled'
        WHERE listing_type = 'plugin' AND status <> 'uninstalled'
    """)
    for column in ("instance_name", "current_release_id", "approved_permissions"):
        op.alter_column("plugin_installs", column, nullable=False)
    op.alter_column("plugin_releases", "plugin_key", nullable=False)
    op.drop_constraint("uq_plugin_install_key", "plugin_installs", type_="unique")
    op.create_unique_constraint(
        "uq_plugin_install_name", "plugin_installs", ["workspace_id", "instance_name"]
    )
    op.drop_constraint("fk_plugin_binding_release_scope", "plugin_bindings", type_="foreignkey")
    op.drop_constraint("uq_plugin_release_capability", "plugin_bindings", type_="unique")
    op.drop_column("plugin_bindings", "release_id")
    op.drop_constraint("ck_plugin_binding_kind", "plugin_bindings", type_="check")
    op.create_check_constraint(
        "ck_plugin_binding_kind", "plugin_bindings", "kind IN ('channel', 'mcp_server', 'skill')"
    )
    op.create_foreign_key(
        "fk_plugin_binding_install_scope",
        "plugin_bindings",
        "plugin_installs",
        ["workspace_id", "install_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.add_column(
        "plugin_bindings",
        sa.Column("configuration_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "workspace_skill_installs",
        sa.Column("configuration_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute("""
        UPDATE plugin_bindings b SET configuration_revision = m.configuration_version
        FROM mcp_servers m WHERE b.kind = 'mcp_server'
            AND b.workspace_id = m.workspace_id AND b.resource_id = m.id
    """)
    op.alter_column("plugin_bindings", "configuration_revision", server_default=None)
    op.create_check_constraint(
        "ck_plugin_binding_revision", "plugin_bindings", "configuration_revision > 0"
    )
    op.alter_column("workspace_skill_installs", "configuration_revision", server_default=None)
    op.drop_constraint("fk_plugin_release_install_scope", "plugin_releases", type_="foreignkey")
    op.drop_constraint("uq_plugin_release_version", "plugin_releases", type_="unique")
    op.drop_constraint("uq_plugin_release_scope", "plugin_releases", type_="unique")
    op.drop_column("plugin_releases", "install_id")
    op.drop_column("plugin_releases", "approved_permissions")
    op.create_unique_constraint(
        "uq_plugin_release_version", "plugin_releases", ["workspace_id", "plugin_key", "version"]
    )
    op.create_unique_constraint(
        "uq_plugin_release_scope", "plugin_releases", ["workspace_id", "plugin_key", "id"]
    )
    op.create_foreign_key(
        "fk_plugin_install_current_release",
        "plugin_installs",
        "plugin_releases",
        ["workspace_id", "plugin_key", "current_release_id"],
        ["workspace_id", "plugin_key", "id"],
    )


def downgrade() -> None:
    # Refuse lossy conversion; remove unsupported new instances/bindings explicitly first.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM plugin_installs GROUP BY workspace_id, plugin_key
                       HAVING count(*) > 1)
                OR EXISTS (SELECT 1 FROM plugin_bindings WHERE kind = 'channel')
                OR EXISTS (SELECT 1 FROM plugin_bindings GROUP BY install_id, capability_key
                           HAVING count(*) > 1) THEN
                RAISE EXCEPTION 'Downgrade cannot represent multiple installations, channel '
                    'bindings, or multiple resources per capability';
            END IF;
            IF EXISTS (SELECT 1 FROM plugin_releases r WHERE NOT EXISTS (
                SELECT 1 FROM plugin_installs i WHERE i.workspace_id = r.workspace_id
                    AND i.plugin_key = r.plugin_key)) THEN
                RAISE EXCEPTION 'Downgrade cannot represent a release without an installation';
            END IF;
        END $$
    """)
    op.drop_constraint("fk_plugin_install_current_release", "plugin_installs", type_="foreignkey")
    op.add_column("plugin_releases", sa.Column("install_id", sa.Uuid()))
    op.add_column("plugin_releases", sa.Column("approved_permissions", JSONB()))
    op.execute("""
        UPDATE plugin_releases r SET install_id = i.id, approved_permissions = i.approved_permissions
        FROM plugin_installs i WHERE i.workspace_id = r.workspace_id AND i.plugin_key = r.plugin_key
    """)
    op.alter_column("plugin_releases", "install_id", nullable=False)
    op.alter_column("plugin_releases", "approved_permissions", nullable=False)
    op.drop_constraint("uq_plugin_release_version", "plugin_releases", type_="unique")
    op.drop_constraint("uq_plugin_release_scope", "plugin_releases", type_="unique")
    op.create_unique_constraint(
        "uq_plugin_release_version", "plugin_releases", ["install_id", "version"]
    )
    op.create_unique_constraint(
        "uq_plugin_release_scope", "plugin_releases", ["workspace_id", "install_id", "id"]
    )
    op.create_foreign_key(
        "fk_plugin_release_install_scope",
        "plugin_releases",
        "plugin_installs",
        ["workspace_id", "install_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.drop_column("plugin_releases", "plugin_key")
    op.add_column("plugin_bindings", sa.Column("release_id", sa.Uuid()))
    op.execute("""
        UPDATE plugin_bindings b SET release_id = i.current_release_id
        FROM plugin_installs i WHERE i.workspace_id = b.workspace_id AND i.id = b.install_id
    """)
    op.alter_column("plugin_bindings", "release_id", nullable=False)
    op.drop_constraint("fk_plugin_binding_install_scope", "plugin_bindings", type_="foreignkey")
    op.create_foreign_key(
        "fk_plugin_binding_release_scope",
        "plugin_bindings",
        "plugin_releases",
        ["workspace_id", "install_id", "release_id"],
        ["workspace_id", "install_id", "id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_plugin_release_capability", "plugin_bindings", ["release_id", "capability_key"]
    )
    op.drop_constraint("ck_plugin_binding_kind", "plugin_bindings", type_="check")
    op.create_check_constraint(
        "ck_plugin_binding_kind",
        "plugin_bindings",
        "kind IN ('mcp_server', 'skill', 'message_trigger', 'reply_channel')",
    )
    op.drop_constraint("ck_plugin_binding_revision", "plugin_bindings", type_="check")
    op.drop_column("plugin_bindings", "configuration_revision")
    op.drop_column("workspace_skill_installs", "configuration_revision")
    op.drop_constraint("uq_plugin_install_name", "plugin_installs", type_="unique")
    op.create_unique_constraint(
        "uq_plugin_install_key", "plugin_installs", ["workspace_id", "plugin_key"]
    )
    for column in ("instance_name", "current_release_id", "approved_permissions"):
        op.drop_column("plugin_installs", column)
