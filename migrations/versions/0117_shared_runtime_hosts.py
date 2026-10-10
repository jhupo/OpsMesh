"""Separate Runtime host lifetime from run and MCP process ownership."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0117_shared_runtime_hosts"
down_revision = "0116_execution_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM runtime_leases WHERE status = 'leased'")):
        raise RuntimeError("Finish or cancel active Runtime runs before upgrading")
    if connection.scalar(sa.text("SELECT count(*) FROM mcp_deployments WHERE runtime_id IS NULL")):
        raise RuntimeError(
            "Bind every managed MCP deployment to an existing Runtime before upgrading"
        )
    op.create_table(
        "runtime_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_runtime_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_kind", sa.String(16), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workspace_runtime_id"],
            ["workspace_runtimes.workspace_id", "workspace_runtimes.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id", "owner_kind", "owner_id", name="uq_runtime_allocation_owner"
        ),
        sa.CheckConstraint("owner_kind in ('run', 'mcp')", name="runtime_allocation_kind_valid"),
    )
    op.create_index(
        "ix_runtime_allocations_host",
        "runtime_allocations",
        ["workspace_id", "workspace_runtime_id"],
    )
    op.execute("""INSERT INTO runtime_allocations (id, workspace_id, workspace_runtime_id, owner_kind, owner_id)
        SELECT gen_random_uuid(), workspace_id, runtime_id, 'mcp', id
        FROM mcp_deployments WHERE status IN ('running', 'starting')""")
    op.execute("""UPDATE agent_runs r SET execution_runtime_id = c.execution_pool_member_id
        FROM workspace_runtimes c WHERE r.execution_runtime_id = c.id
        AND c.execution_pool_member_id IS NOT NULL""")
    op.execute("""UPDATE workspace_runtimes SET status='deleted', connection_status='offline',
        docker_container_id=NULL WHERE execution_pool_member_id IS NOT NULL""")
    op.execute("""UPDATE agent_runs SET input = jsonb_set(input, '{runtime_execution}',
        ((input->'runtime_execution') - 'pool_member_runtime_id') ||
        jsonb_build_object('runtime_id', execution_runtime_id::text))
        WHERE input->'runtime_execution'->>'mode'='pooled' AND execution_runtime_id IS NOT NULL""")
    op.drop_index("ix_workspace_runtimes_pool_member", table_name="workspace_runtimes")
    op.drop_column("workspace_runtimes", "execution_pool_member_id")
    op.alter_column("mcp_deployments", "runtime_id", nullable=False)
    op.drop_column("mcp_deployments", "template_id")
    op.drop_column("mcp_deployments", "network_disabled")
    op.execute("""UPDATE workspace_runtimes SET limits = limits ||
        jsonb_build_object('max_concurrent_executions', 16)""")
    op.execute("""UPDATE runtime_templates SET default_limits = default_limits ||
        jsonb_build_object('max_concurrent_executions', 16)""")
    # Container leases describe host lifecycle only, never process admission.
    op.execute("""UPDATE runtime_leases SET metadata = metadata - 'pool'""")
    op.execute("""UPDATE workspace_runtimes SET capabilities =
        capabilities - 'managed_mcp_server_id'""")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM runtime_allocations")):
        raise RuntimeError("Stop every Runtime task and managed MCP process before downgrading")
    op.add_column(
        "workspace_runtimes", sa.Column("execution_pool_member_id", postgresql.UUID(as_uuid=True))
    )
    op.create_foreign_key(
        None,
        "workspace_runtimes",
        "workspace_runtimes",
        ["execution_pool_member_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_workspace_runtimes_pool_member",
        "workspace_runtimes",
        ["workspace_id", "execution_pool_member_id"],
    )
    op.add_column("mcp_deployments", sa.Column("template_id", postgresql.UUID(as_uuid=True)))
    op.add_column("mcp_deployments", sa.Column("network_disabled", sa.Boolean()))
    op.execute("""UPDATE mcp_deployments d SET template_id = r.runtime_template_id,
        network_disabled = r.network_policy->>'mode' = 'none' FROM workspace_runtimes r
        WHERE d.runtime_id = r.id""")
    op.alter_column("mcp_deployments", "template_id", nullable=False)
    op.alter_column("mcp_deployments", "network_disabled", nullable=False)
    op.create_foreign_key(None, "mcp_deployments", "runtime_templates", ["template_id"], ["id"])
    op.alter_column("mcp_deployments", "runtime_id", nullable=True)
    op.execute("UPDATE workspace_runtimes SET limits = limits - 'max_concurrent_executions'")
    op.execute(
        "UPDATE runtime_templates SET default_limits = default_limits - 'max_concurrent_executions'"
    )
    op.drop_table("runtime_allocations")
