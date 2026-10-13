"""Native SDK plugin process references and explicit uncertainty recovery.

Revision ID: 0135_native_plugin_deployments
Revises: 0134_native_platform_operations
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0135_native_plugin_deployments"
down_revision = "0134_native_platform_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plugin_deployments", sa.Column("sandbox_reference_id", postgresql.UUID(), nullable=True)
    )
    op.add_column("plugin_deployments", sa.Column("process_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_plugin_deployment_sandbox_scope",
        "plugin_deployments",
        "sandbox_references",
        ["workspace_id", "sandbox_reference_id"],
        ["workspace_id", "id"],
    )
    op.drop_index("ix_plugin_deployments_due", table_name="plugin_deployments")
    op.drop_column("plugin_deployments", "next_check_at")
    op.drop_constraint(
        op.f("ck_sandbox_references_sandbox_owner_kind"), "sandbox_references", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_sandbox_references_sandbox_owner_kind"),
        "sandbox_references",
        "owner_kind IN ('run', 'environment', 'mcp', 'command', 'plugin')",
    )
    # Old process outcomes are not replay-safe and their image CMD was never an
    # explicit native process contract. Keep the records for operator reconciliation.
    op.execute(
        sa.text("""
        UPDATE plugin_deployments SET status='unknown', error_code='plugin_native_migration_reconciliation'
        WHERE status NOT IN ('stopped', 'failed', 'blocked') OR runtime_id IS NOT NULL
    """)
    )
    op.execute(
        sa.text("""
        UPDATE plugin_credentials SET status='revoked'
        WHERE id IN (SELECT credential_id FROM plugin_deployments WHERE status='unknown')
    """)
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(
        sa.text(
            "SELECT count(*) FROM sandbox_references WHERE owner_kind='plugin' AND status <> 'deleted'"
        )
    ):
        raise RuntimeError("Stop and reconcile native plugin processes before downgrade")
    op.execute(sa.text("UPDATE plugin_deployments SET sandbox_reference_id=NULL"))
    op.execute(sa.text("DELETE FROM sandbox_references WHERE owner_kind='plugin'"))
    op.drop_constraint(
        op.f("ck_sandbox_references_sandbox_owner_kind"), "sandbox_references", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_sandbox_references_sandbox_owner_kind"),
        "sandbox_references",
        "owner_kind IN ('run', 'environment', 'mcp', 'command')",
    )
    op.add_column(
        "plugin_deployments",
        sa.Column(
            "next_check_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.alter_column("plugin_deployments", "next_check_at", server_default=None)
    op.create_index("ix_plugin_deployments_due", "plugin_deployments", ["next_check_at"])
    op.drop_constraint(
        "fk_plugin_deployment_sandbox_scope", "plugin_deployments", type_="foreignkey"
    )
    op.drop_column("plugin_deployments", "process_id")
    op.drop_column("plugin_deployments", "sandbox_reference_id")
