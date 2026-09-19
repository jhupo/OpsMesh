"""Scoped plugin service credentials, private state and message origin."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0100_plugin_services"
down_revision = "0099_resource_authorization"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    op.create_table(
        "plugin_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("install_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("permissions", JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["workspace_id", "install_id"],
            ["plugin_installs.workspace_id", "plugin_installs.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("token_hash", name="uq_plugin_credential_hash"),
    )
    op.create_index(
        "ix_plugin_credentials_install", "plugin_credentials", ["workspace_id", "install_id"]
    )
    op.create_table(
        "plugin_values",
        sa.Column("workspace_id", sa.Uuid(), primary_key=True),
        sa.Column("install_id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("value", JSONB(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["workspace_id", "install_id"],
            ["plugin_installs.workspace_id", "plugin_installs.id"],
            ondelete="CASCADE",
        ),
    )
    op.add_column("automation_events", sa.Column("source_install_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_automation_event_plugin_scope",
        "automation_events",
        "plugin_installs",
        ["workspace_id", "source_install_id"],
        ["workspace_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_automation_event_plugin_scope", "automation_events", type_="foreignkey")
    op.drop_column("automation_events", "source_install_id")
    op.drop_table("plugin_values")
    op.drop_table("plugin_credentials")
