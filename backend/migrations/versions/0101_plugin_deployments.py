"""Durable isolated plugin processes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0101_plugin_deployments"
down_revision = "0100_plugin_services"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_workspace_runtime_scope", "workspace_runtimes", ["workspace_id", "id"]
    )
    op.create_table(
        "plugin_deployments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("install_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("runtime_templates.id"), nullable=False),
        sa.Column("image", sa.String(260), nullable=False),
        sa.Column("desired_state", sa.String(20), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("applied_revision", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("execution_identity", JSONB(), nullable=False),
        sa.Column("configuration", JSONB(), nullable=False),
        sa.Column("encrypted_environment", sa.Text(), nullable=False),
        sa.Column("encryption_key_id", sa.String(120), nullable=False),
        sa.Column("runtime_id", sa.Uuid(), nullable=True),
        sa.Column(
            "credential_id", sa.Uuid(), sa.ForeignKey("plugin_credentials.id"), nullable=True
        ),
        sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        *[
            sa.Column(n, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for n in ("created_at", "updated_at")
        ],
        sa.UniqueConstraint("workspace_id", "install_id", name="uq_plugin_deployment_install"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "install_id"], ["plugin_installs.workspace_id", "plugin_installs.id"]
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "runtime_id"],
            ["workspace_runtimes.workspace_id", "workspace_runtimes.id"],
        ),
        sa.CheckConstraint("desired_state IN ('running', 'stopped')", name="deployment_desired"),
        sa.CheckConstraint("revision > 0", name="deployment_revision"),
    )
    op.create_index("ix_plugin_deployments_due", "plugin_deployments", ["next_check_at"])


def downgrade() -> None:
    op.drop_table("plugin_deployments")
    op.drop_constraint("uq_workspace_runtime_scope", "workspace_runtimes", type_="unique")
