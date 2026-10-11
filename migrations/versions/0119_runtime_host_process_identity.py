"""Separate physical Runtime hosts from execution policies and allocate kernel identities."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0119_runtime_host_process_identity"
down_revision = "0118_shared_runtime_execution_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A deployment must drain before this migration. Running old processes do not
    # have kernel identities and cannot be safely adopted by the new supervisor.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM runtime_allocations) THEN
            RAISE EXCEPTION 'Drain Runtime executions before upgrading host identities';
          END IF;
        END $$
    """)
    op.create_table(
        "runtime_hosts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("host_key", sa.String(128), nullable=False),
        sa.Column("image", sa.String(260), nullable=False),
        sa.Column("docker_container_id", sa.String(120)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("resources", postgresql.JSONB(), nullable=False),
        sa.Column("provisioning_owner_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_runtime_host_scope"),
        sa.CheckConstraint("capacity between 1 and 128", name="runtime_host_capacity_valid"),
    )
    op.create_index(
        "uq_runtime_host_node_key",
        "runtime_hosts",
        ["node_id", "workspace_id", "host_key"],
        unique=True,
        postgresql_where=sa.text("status not in ('deleted','failed')"),
    )
    op.add_column("workspace_runtimes", sa.Column("host_id", sa.Uuid()))
    op.drop_constraint("runtime_allocation_kind_valid", "runtime_allocations", type_="check")
    op.create_check_constraint(
        "runtime_allocation_kind_valid",
        "runtime_allocations",
        "owner_kind in ('run', 'mcp', 'command')",
    )
    # Preserve each old host's ownership for explicit removal during cutover.
    # Reprovisioning builds a new shared host; no legacy execution path remains.
    op.execute("""
        UPDATE runtime_templates SET default_network_policy=
          default_network_policy - 'gateway_network' - 'proxy_url' - 'disabled';
        UPDATE workspace_runtimes SET network_policy=
          network_policy - 'gateway_network' - 'proxy_url' - 'disabled';
        INSERT INTO runtime_hosts
          (id,workspace_id,node_id,host_key,image,docker_container_id,status,capacity,resources,created_at,updated_at)
        SELECT r.id,r.workspace_id,coalesce(r.capabilities->>'node_id','unassigned'),
          r.id::text,coalesce(t.image,'unavailable'),r.docker_container_id,'requires_reprovision',
          16,coalesce(r.capabilities->'managed_resources','{}'::jsonb),r.created_at,r.updated_at
        FROM workspace_runtimes r LEFT JOIN runtime_templates t ON t.id=r.runtime_template_id
        WHERE r.docker_container_id IS NOT NULL;
        UPDATE workspace_runtimes SET host_id=id,status='stopped',connection_status='offline'
          WHERE docker_container_id IS NOT NULL;
        UPDATE workspace_runtimes SET capabilities=capabilities-'managed_resources';
        UPDATE runtime_space_reservations SET reservation_key=
          replace(reservation_key,'workspace_runtime:','runtime_host:')
          WHERE reservation_key LIKE 'workspace_runtime:%';
    """)
    op.create_foreign_key(
        "fk_runtime_host_scope",
        "workspace_runtimes",
        "runtime_hosts",
        ["workspace_id", "host_id"],
        ["workspace_id", "id"],
    )
    op.create_index("ix_workspace_runtimes_host", "workspace_runtimes", ["host_id"])
    op.drop_index("ix_workspace_runtimes_container", table_name="workspace_runtimes")
    op.drop_column("workspace_runtimes", "docker_container_id")
    op.add_column("runtime_allocations", sa.Column("host_id", sa.Uuid(), nullable=False))
    op.add_column("runtime_allocations", sa.Column("execution_uid", sa.Integer(), nullable=False))
    op.add_column(
        "runtime_allocations", sa.Column("network_policy", postgresql.JSONB(), nullable=False)
    )
    op.create_foreign_key(
        "fk_allocation_host_scope",
        "runtime_allocations",
        "runtime_hosts",
        ["workspace_id", "host_id"],
        ["workspace_id", "id"],
    )
    op.create_unique_constraint(
        "uq_runtime_allocation_identity", "runtime_allocations", ["host_id", "execution_uid"]
    )
    op.create_check_constraint(
        "runtime_allocation_uid_valid",
        "runtime_allocations",
        "execution_uid between 100000 and 100127",
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM runtime_allocations) THEN
            RAISE EXCEPTION 'Drain Runtime executions before downgrading host identities';
          END IF;
        END $$
    """)
    op.drop_constraint("runtime_allocation_uid_valid", "runtime_allocations", type_="check")
    op.drop_constraint("runtime_allocation_kind_valid", "runtime_allocations", type_="check")
    op.create_check_constraint(
        "runtime_allocation_kind_valid", "runtime_allocations", "owner_kind in ('run', 'mcp')"
    )
    op.drop_constraint("uq_runtime_allocation_identity", "runtime_allocations", type_="unique")
    op.drop_constraint("fk_allocation_host_scope", "runtime_allocations", type_="foreignkey")
    for column in ("network_policy", "execution_uid", "host_id"):
        op.drop_column("runtime_allocations", column)
    op.add_column("workspace_runtimes", sa.Column("docker_container_id", sa.String(120)))
    op.execute("""
        UPDATE workspace_runtimes r SET docker_container_id=h.docker_container_id
        FROM runtime_hosts h WHERE h.id=r.host_id;
        UPDATE workspace_runtimes r SET capabilities=r.capabilities ||
          jsonb_build_object('managed_resources',h.resources)
        FROM runtime_hosts h WHERE h.id=r.host_id;
    """)
    op.create_index(
        "ix_workspace_runtimes_container", "workspace_runtimes", ["docker_container_id"]
    )
    op.drop_index("ix_workspace_runtimes_host", table_name="workspace_runtimes")
    op.drop_constraint("fk_runtime_host_scope", "workspace_runtimes", type_="foreignkey")
    op.drop_column("workspace_runtimes", "host_id")
    op.drop_table("runtime_hosts")
    op.execute(
        "UPDATE runtime_space_reservations SET reservation_key="
        "replace(reservation_key,'runtime_host:','workspace_runtime:') "
        "WHERE reservation_key LIKE 'runtime_host:%'"
    )
