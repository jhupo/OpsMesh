"""Replace platform Runtime hosts, leases and UID allocations with SDK references."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0124_native_sandbox_references"
down_revision = "0123_mcp_call_diagnostics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM runtime_allocations)
        OR EXISTS (SELECT 1 FROM worker_leases WHERE status='running')
        OR EXISTS (SELECT 1 FROM queue_dispatches WHERE published_at IS NULL)
        OR EXISTS (SELECT 1 FROM agent_runs WHERE status IN
          ('queued','running','waiting_runtime','waiting_approval','waiting_subworkflow'))
        OR EXISTS (SELECT 1 FROM runtime_commands WHERE status IN
          ('queued','running','waiting_approval'))
        OR EXISTS (SELECT 1 FROM self_hosted_job_claims WHERE status='claimed')
        OR EXISTS (SELECT 1 FROM self_hosted_mcp_jobs WHERE status IN ('queued','claimed','running'))
      THEN RAISE EXCEPTION 'Drain old Runtime and Worker executions before upgrading'; END IF;
    END $$""")
    op.add_column(
        "runtime_commands",
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        None, "runtime_commands", "users", ["requested_by_user_id"], ["id"], ondelete="SET NULL"
    )
    op.execute("ALTER TABLE agent_runs DROP COLUMN execution_runtime_id CASCADE")
    op.add_column(
        "runtime_templates", sa.Column("sandbox_backend_id", sa.String(64), nullable=True)
    )
    op.add_column(
        "runtime_templates", sa.Column("sandbox_options", postgresql.JSONB(), nullable=True)
    )
    op.execute(
        """UPDATE runtime_templates
        SET sandbox_backend_id='docker', sandbox_options=jsonb_build_object('image', image, 'network_mode', 'none')
        WHERE sandbox_backend_id IS NULL"""
    )
    op.alter_column("runtime_templates", "sandbox_backend_id", nullable=False)
    op.alter_column("runtime_templates", "sandbox_options", nullable=False)
    op.drop_column("runtime_templates", "default_limits")
    op.drop_column("runtime_templates", "default_network_policy")

    op.add_column(
        "workspace_runtimes", sa.Column("sandbox_backend_id", sa.String(64), nullable=True)
    )
    op.add_column(
        "workspace_runtimes", sa.Column("sandbox_options", postgresql.JSONB(), nullable=True)
    )
    op.execute(
        """UPDATE workspace_runtimes r
        SET sandbox_backend_id=coalesce(t.sandbox_backend_id, 'docker'),
            sandbox_options=coalesce(t.sandbox_options, jsonb_build_object('image', t.image))
        FROM runtime_templates t WHERE r.runtime_template_id=t.id"""
    )
    op.execute(
        """UPDATE workspace_runtimes
        SET sandbox_backend_id=coalesce(sandbox_backend_id, 'docker'),
            sandbox_options=coalesce(sandbox_options, '{}'::jsonb)"""
    )
    op.alter_column("workspace_runtimes", "sandbox_backend_id", nullable=False)
    op.alter_column("workspace_runtimes", "sandbox_options", nullable=False)
    op.execute(
        """ALTER TABLE workspace_runtimes
        DROP COLUMN IF EXISTS host_id CASCADE,
        DROP COLUMN IF EXISTS execution_run_id CASCADE,
        DROP COLUMN IF EXISTS parent_runtime_id CASCADE,
        DROP COLUMN IF EXISTS execution_mode CASCADE,
        DROP COLUMN IF EXISTS runtime_provider CASCADE,
        DROP COLUMN IF EXISTS runtime_type CASCADE,
        DROP COLUMN IF EXISTS connection_status CASCADE,
        DROP COLUMN IF EXISTS docker_container_id CASCADE,
        DROP COLUMN IF EXISTS limits CASCADE,
        DROP COLUMN IF EXISTS network_policy CASCADE,
        DROP COLUMN IF EXISTS capabilities CASCADE,
        DROP COLUMN IF EXISTS last_heartbeat_at CASCADE"""
    )
    op.execute(
        "UPDATE workspace_runtimes SET status=CASE WHEN status='deleted' THEN 'deleted' ELSE 'disabled' END"
    )
    op.create_check_constraint(
        "workspace_runtime_status_valid",
        "workspace_runtimes",
        "status IN ('active','disabled','deleted')",
    )
    op.create_index(
        "ix_workspace_runtimes_workspace_backend",
        "workspace_runtimes",
        ["workspace_id", "sandbox_backend_id"],
    )
    for table in reversed(_RETIRED_TABLES):
        op.drop_table(table)
    op.execute("DELETE FROM platform_policies WHERE policy_key='global_worker_control'")
    op.execute(
        "UPDATE platform_policies SET value=value-'allow_self_hosted_runtimes' WHERE policy_key='global_risky_execution'"
    )
    op.execute(
        "UPDATE mcp_servers SET connection=connection-'restart_policy' "
        "WHERE connection->>'runtime'='managed'"
    )
    op.execute("UPDATE mcp_deployments SET status='stopped', action='stop', last_error=NULL")
    op.drop_column("mcp_deployments", "execution_identity")
    op.drop_constraint("cleanup_status_valid", "agent_run_project_io_states", type_="check")
    for column in ("cleanup_status", "cleanup_attempts", "cleanup_error", "cleaned_at"):
        op.drop_column("agent_run_project_io_states", column)
    op.execute("UPDATE agent_run_project_io_states SET status='failed' WHERE status='harvesting'")
    op.drop_constraint("status_valid", "agent_run_project_io_states", type_="check")
    op.create_check_constraint(
        "status_valid",
        "agent_run_project_io_states",
        "status in ('pending', 'staged', 'harvested', 'failed')",
    )
    op.execute(
        "UPDATE platform_policies SET value=value-ARRAY['worker_stale_after_seconds',"
        "'queue_scan_limit','docker_control_timeout_seconds','file_transfer_timeout_seconds',"
        "'url_fetch_timeout_seconds'] WHERE policy_key='operational_configuration'"
    )

    op.create_table(
        "sandbox_references",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_runtime_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_kind", sa.String(16), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("backend_id", sa.String(64), nullable=False),
        sa.Column("native_session_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("encrypted_session_state", sa.Text(), nullable=True),
        sa.Column("encryption_key_id", sa.String(80), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workspace_runtime_id"],
            ["workspace_runtimes.workspace_id", "workspace_runtimes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "owner_kind", "owner_id", name="uq_sandbox_reference_owner"
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_sandbox_reference_scope"),
        sa.CheckConstraint(
            "owner_kind IN ('run', 'environment', 'mcp', 'command')", name="sandbox_owner_kind"
        ),
        sa.CheckConstraint(
            "status IN ('provisioning', 'running', 'stopped', 'deleting', 'deleted', 'unknown')",
            name="sandbox_reference_status",
        ),
    )
    op.create_index(
        "ix_sandbox_references_runtime",
        "sandbox_references",
        ["workspace_id", "workspace_runtime_id", "status"],
    )
    op.create_table(
        "sdk_session_operations",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.String(240), nullable=False),
        sa.Column("operation_id", sa.String(512), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["persistent_agent_sessions.session_key"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "operation_id", name="uq_sdk_session_operation"),
    )
    op.create_index(
        "ix_sdk_session_operations_session",
        "sdk_session_operations",
        ["workspace_id", "session_id"],
    )
    op.create_index(
        "ix_sdk_session_operations_run", "sdk_session_operations", ["workspace_id", "run_id"]
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT count(*) FROM sandbox_references WHERE status <> 'deleted'")
    ):
        raise RuntimeError("Stop and delete native SDK sandbox sessions before downgrading")
    op.drop_index("ix_sdk_session_operations_run", table_name="sdk_session_operations")
    op.drop_index("ix_sdk_session_operations_session", table_name="sdk_session_operations")
    op.drop_table("sdk_session_operations")
    op.drop_index("ix_sandbox_references_runtime", table_name="sandbox_references")
    op.drop_table("sandbox_references")
    op.add_column(
        "mcp_deployments",
        sa.Column("execution_identity", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.alter_column("mcp_deployments", "execution_identity", server_default=None)
    op.drop_constraint("status_valid", "agent_run_project_io_states", type_="check")
    op.create_check_constraint(
        "status_valid",
        "agent_run_project_io_states",
        "status in ('pending', 'staged', 'harvesting', 'harvested', 'failed')",
    )
    op.add_column(
        "agent_run_project_io_states",
        sa.Column("cleanup_status", sa.String(32), nullable=False, server_default="not_required"),
    )
    op.add_column(
        "agent_run_project_io_states",
        sa.Column("cleanup_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_run_project_io_states", sa.Column("cleanup_error", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "agent_run_project_io_states",
        sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "cleanup_status_valid",
        "agent_run_project_io_states",
        "cleanup_status in ('pending', 'running', 'completed', 'failed', 'not_required')",
    )
    op.alter_column("agent_run_project_io_states", "cleanup_status", server_default=None)
    op.alter_column("agent_run_project_io_states", "cleanup_attempts", server_default=None)

    for statement in _RETIRED_SCHEMA:
        op.execute(statement)
    op.drop_constraint("workspace_runtime_status_valid", "workspace_runtimes", type_="check")
    op.drop_index("ix_workspace_runtimes_workspace_backend", table_name="workspace_runtimes")
    op.add_column("workspace_runtimes", sa.Column("host_id", postgresql.UUID(as_uuid=True)))
    op.add_column(
        "workspace_runtimes",
        sa.Column(
            "parent_runtime_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_runtimes.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "workspace_runtimes",
        sa.Column(
            "execution_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "workspace_runtimes",
        sa.Column("execution_mode", sa.String(16), nullable=False, server_default="shared"),
    )
    op.create_check_constraint(
        "workspace_runtime_execution_mode_valid",
        "workspace_runtimes",
        "execution_mode IN ('isolated','shared')",
    )
    op.create_index(
        "ix_workspace_runtimes_workspace_execution_run",
        "workspace_runtimes",
        ["workspace_id", "execution_run_id"],
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "execution_runtime_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_runtimes.id", ondelete="SET NULL"),
        ),
    )
    op.create_index(
        "ix_agent_runs_workspace_execution_runtime",
        "agent_runs",
        ["workspace_id", "execution_runtime_id"],
    )
    op.drop_column("runtime_commands", "requested_by_user_id")
    for column in ("runtime_provider", "runtime_type", "connection_status"):
        op.add_column("workspace_runtimes", sa.Column(column, sa.String(32)))
    for column in ("limits", "network_policy", "capabilities"):
        op.add_column("workspace_runtimes", sa.Column(column, postgresql.JSONB()))
    op.add_column("workspace_runtimes", sa.Column("last_heartbeat_at", sa.DateTime()))
    op.execute("""UPDATE workspace_runtimes SET runtime_provider='cloud_docker', runtime_type='docker',
        status=CASE WHEN status='deleted' THEN 'deleted' ELSE 'stopped' END,
        connection_status='offline', limits='{}', network_policy='{"mode":"none"}', capabilities='{}'""")
    for column in (
        "runtime_provider",
        "runtime_type",
        "connection_status",
        "limits",
        "network_policy",
        "capabilities",
    ):
        op.alter_column("workspace_runtimes", column, nullable=False)
    op.create_foreign_key(
        "fk_runtime_host_scope",
        "workspace_runtimes",
        "runtime_hosts",
        ["workspace_id", "host_id"],
        ["workspace_id", "id"],
    )
    op.create_index("ix_workspace_runtimes_host", "workspace_runtimes", ["host_id"])
    op.create_index(
        "ix_workspace_runtimes_workspace_provider",
        "workspace_runtimes",
        ["workspace_id", "runtime_provider", "status"],
    )
    op.add_column(
        "runtime_templates", sa.Column("default_limits", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "runtime_templates", sa.Column("default_network_policy", postgresql.JSONB(), nullable=True)
    )
    op.execute("UPDATE runtime_templates SET default_limits='{}', default_network_policy='{}'")
    op.alter_column("runtime_templates", "default_limits", nullable=False)
    op.alter_column("runtime_templates", "default_network_policy", nullable=False)
    op.drop_column("runtime_templates", "sandbox_options")
    op.drop_column("runtime_templates", "sandbox_backend_id")
    op.drop_column("workspace_runtimes", "sandbox_options")
    op.drop_column("workspace_runtimes", "sandbox_backend_id")


_RETIRED_TABLES = (
    "runtime_hosts",
    "runtime_leases",
    "runtime_allocations",
    "worker_heartbeats",
    "worker_nodes",
    "worker_leases",
    "runtime_enrollment_tokens",
    "runtime_credentials",
    "self_hosted_workers",
    "self_hosted_job_claims",
    "self_hosted_mcp_jobs",
    "local_file_references",
    "self_hosted_artifact_uploads",
)

_RETIRED_SCHEMA = (
    """CREATE TABLE runtime_hosts (
	workspace_id UUID NOT NULL, 
	node_id VARCHAR(128) NOT NULL, 
	host_key VARCHAR(128) NOT NULL, 
	image VARCHAR(260) NOT NULL, 
	docker_container_id VARCHAR(120), 
	status VARCHAR(32) NOT NULL, 
	capacity INTEGER NOT NULL, 
	resources JSONB NOT NULL, 
	provisioning_owner_id UUID, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_runtime_hosts PRIMARY KEY (id), 
	CONSTRAINT uq_runtime_host_scope UNIQUE (workspace_id, id), 
	CONSTRAINT ck_runtime_hosts_runtime_host_capacity_valid CHECK (capacity between 1 and 128), 
	CONSTRAINT fk_runtime_hosts_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE
)""",
    """CREATE UNIQUE INDEX uq_runtime_host_node_key ON runtime_hosts (node_id, workspace_id, host_key) WHERE status not in ('deleted','failed')""",
    """CREATE TABLE runtime_leases (
	workspace_id UUID NOT NULL, 
	workspace_runtime_id UUID NOT NULL, 
	runtime_space_id UUID, 
	docker_container_id VARCHAR(120), 
	status VARCHAR(32) NOT NULL, 
	metadata JSONB NOT NULL, 
	acquired_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	released_at TIMESTAMP WITHOUT TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_runtime_leases PRIMARY KEY (id), 
	CONSTRAINT uq_runtime_leases_runtime UNIQUE (workspace_runtime_id), 
	CONSTRAINT fk_runtime_leases_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	CONSTRAINT fk_runtime_leases_workspace_runtime_id_workspace_runtimes FOREIGN KEY(workspace_runtime_id) REFERENCES workspace_runtimes (id) ON DELETE CASCADE, 
	CONSTRAINT fk_runtime_leases_runtime_space_id_runtime_spaces FOREIGN KEY(runtime_space_id) REFERENCES runtime_spaces (id) ON DELETE SET NULL
)""",
    """CREATE INDEX ix_runtime_leases_container ON runtime_leases (docker_container_id)""",
    """CREATE INDEX ix_runtime_leases_runtime_space ON runtime_leases (workspace_id, runtime_space_id)""",
    """CREATE INDEX ix_runtime_leases_workspace_status ON runtime_leases (workspace_id, status)""",
    """CREATE TABLE runtime_allocations (
	workspace_id UUID NOT NULL, 
	workspace_runtime_id UUID NOT NULL, 
	owner_kind VARCHAR(16) NOT NULL, 
	owner_id UUID NOT NULL, 
	host_id UUID NOT NULL, 
	execution_uid INTEGER NOT NULL, 
	network_policy JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_runtime_allocations PRIMARY KEY (id), 
	CONSTRAINT fk_runtime_allocations_workspace_id_workspace_runtimes FOREIGN KEY(workspace_id, workspace_runtime_id) REFERENCES workspace_runtimes (workspace_id, id) ON DELETE CASCADE, 
	CONSTRAINT uq_runtime_allocation_owner UNIQUE (workspace_id, owner_kind, owner_id), 
	CONSTRAINT ck_runtime_allocations_runtime_allocation_kind_valid CHECK (owner_kind in ('run', 'mcp', 'command')), 
	CONSTRAINT fk_allocation_host_scope FOREIGN KEY(workspace_id, host_id) REFERENCES runtime_hosts (workspace_id, id), 
	CONSTRAINT uq_runtime_allocation_identity UNIQUE (host_id, execution_uid), 
	CONSTRAINT ck_runtime_allocations_runtime_allocation_uid_valid CHECK (execution_uid between 100000 and 100127)
)""",
    """CREATE INDEX ix_runtime_allocations_host ON runtime_allocations (workspace_id, workspace_runtime_id)""",
    """CREATE TABLE worker_heartbeats (
	workspace_id UUID, 
	worker_id VARCHAR(160) NOT NULL, 
	worker_type VARCHAR(80) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	queue_name VARCHAR(120) NOT NULL, 
	details JSONB NOT NULL, 
	last_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_worker_heartbeats PRIMARY KEY (id), 
	CONSTRAINT fk_worker_heartbeats_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_worker_heartbeats_worker ON worker_heartbeats (worker_id)""",
    """CREATE INDEX ix_worker_heartbeats_workspace_status ON worker_heartbeats (workspace_id, status)""",
    """CREATE TABLE worker_nodes (
	worker_id VARCHAR(160) NOT NULL, 
	worker_type VARCHAR(80) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	queue_name VARCHAR(120) NOT NULL, 
	worker_version VARCHAR(120), 
	hostname VARCHAR(255), 
	capacity JSONB NOT NULL, 
	details JSONB NOT NULL, 
	drain_requested_at TIMESTAMP WITHOUT TIME ZONE, 
	last_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_worker_nodes PRIMARY KEY (id), 
	CONSTRAINT uq_worker_nodes_worker_id UNIQUE (worker_id)
)""",
    """CREATE INDEX ix_worker_nodes_status ON worker_nodes (status)""",
    """CREATE INDEX ix_worker_nodes_type_status ON worker_nodes (worker_type, status)""",
    """CREATE TABLE worker_leases (
	workspace_id UUID NOT NULL, 
	worker_id VARCHAR(160) NOT NULL, 
	queue_name VARCHAR(120) NOT NULL, 
	job_id UUID NOT NULL, 
	claim_token VARCHAR(64), 
	job_type VARCHAR(80) NOT NULL, 
	resource_id UUID NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	attempt INTEGER NOT NULL, 
	request_id VARCHAR(80), 
	trace_id VARCHAR(32), 
	span_id VARCHAR(16), 
	runtime_id VARCHAR(120), 
	metadata JSONB NOT NULL, 
	started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	last_heartbeat_at TIMESTAMP WITHOUT TIME ZONE, 
	finished_at TIMESTAMP WITHOUT TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_worker_leases PRIMARY KEY (id), 
	CONSTRAINT uq_worker_leases_job_id UNIQUE (job_id), 
	CONSTRAINT ck_worker_leases_status_valid CHECK (status IN ('running', 'completed', 'failed', 'retrying', 'expired')), 
	CONSTRAINT ck_worker_leases_attempt_non_negative CHECK (attempt >= 0), 
	CONSTRAINT fk_worker_leases_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_worker_leases_resource ON worker_leases (workspace_id, job_type, resource_id)""",
    """CREATE INDEX ix_worker_leases_status_heartbeat ON worker_leases (status, last_heartbeat_at)""",
    """CREATE INDEX ix_worker_leases_worker_status ON worker_leases (worker_id, status)""",
    """CREATE INDEX ix_worker_leases_workspace_request ON worker_leases (workspace_id, request_id)""",
    """CREATE INDEX ix_worker_leases_workspace_runtime ON worker_leases (workspace_id, runtime_id)""",
    """CREATE INDEX ix_worker_leases_workspace_status ON worker_leases (workspace_id, status)""",
    """CREATE INDEX ix_worker_leases_workspace_trace ON worker_leases (workspace_id, trace_id)""",
    """CREATE TABLE runtime_enrollment_tokens (
	workspace_id UUID NOT NULL, 
	created_by_user_id UUID, 
	token_hash VARCHAR(128) NOT NULL, 
	name VARCHAR(160) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	expires_at TIMESTAMP WITHOUT TIME ZONE, 
	used_at TIMESTAMP WITHOUT TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_runtime_enrollment_tokens PRIMARY KEY (id), 
	CONSTRAINT uq_runtime_enrollment_tokens_hash UNIQUE (token_hash), 
	CONSTRAINT fk_runtime_enrollment_tokens_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	CONSTRAINT fk_runtime_enrollment_tokens_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
)""",
    """CREATE INDEX ix_runtime_enrollment_tokens_workspace_status ON runtime_enrollment_tokens (workspace_id, status)""",
    """CREATE TABLE runtime_credentials (
	workspace_id UUID NOT NULL, 
	workspace_runtime_id UUID NOT NULL, 
	token_hash VARCHAR(128) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	last_used_at TIMESTAMP WITHOUT TIME ZONE, 
	revoked_at TIMESTAMP WITHOUT TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_runtime_credentials PRIMARY KEY (id), 
	CONSTRAINT uq_runtime_credentials_hash UNIQUE (token_hash), 
	CONSTRAINT fk_runtime_credentials_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	CONSTRAINT fk_runtime_credentials_workspace_runtime_id_workspace_runtimes FOREIGN KEY(workspace_runtime_id) REFERENCES workspace_runtimes (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_runtime_credentials_workspace_status ON runtime_credentials (workspace_id, status)""",
    """CREATE TABLE self_hosted_workers (
	workspace_id UUID NOT NULL, 
	workspace_runtime_id UUID NOT NULL, 
	name VARCHAR(160) NOT NULL, 
	machine_id VARCHAR(160) NOT NULL, 
	version VARCHAR(80) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	capabilities JSONB NOT NULL, 
	capability_attestation_state VARCHAR(32) NOT NULL, 
	capability_attestation_fingerprint VARCHAR(80), 
	capability_attestation_metadata JSONB NOT NULL, 
	capability_attested_at TIMESTAMP WITH TIME ZONE, 
	host_isolation_verified BOOLEAN NOT NULL, 
	last_heartbeat_at TIMESTAMP WITHOUT TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_self_hosted_workers PRIMARY KEY (id), 
	CONSTRAINT uq_self_hosted_workers_runtime UNIQUE (workspace_runtime_id), 
	CONSTRAINT fk_self_hosted_workers_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	CONSTRAINT fk_self_hosted_workers_workspace_runtime_id_workspace_runtimes FOREIGN KEY(workspace_runtime_id) REFERENCES workspace_runtimes (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_self_hosted_workers_workspace_status ON self_hosted_workers (workspace_id, status)""",
    """CREATE TABLE self_hosted_job_claims (
	workspace_id UUID NOT NULL, 
	worker_id UUID NOT NULL, 
	agent_run_id UUID NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	claimed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	completed_at TIMESTAMP WITHOUT TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_self_hosted_job_claims PRIMARY KEY (id), 
	CONSTRAINT uq_self_hosted_job_claims_run UNIQUE (agent_run_id), 
	CONSTRAINT fk_self_hosted_job_claims_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	CONSTRAINT fk_self_hosted_job_claims_worker_id_self_hosted_workers FOREIGN KEY(worker_id) REFERENCES self_hosted_workers (id) ON DELETE CASCADE, 
	CONSTRAINT fk_self_hosted_job_claims_agent_run_id_agent_runs FOREIGN KEY(agent_run_id) REFERENCES agent_runs (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_self_hosted_job_claims_workspace_status ON self_hosted_job_claims (workspace_id, status)""",
    """CREATE INDEX ix_self_hosted_job_claims_workspace_worker ON self_hosted_job_claims (workspace_id, worker_id)""",
    """CREATE TABLE self_hosted_mcp_jobs (
	workspace_id UUID NOT NULL, 
	workspace_runtime_id UUID NOT NULL, 
	worker_id UUID, 
	agent_run_id UUID NOT NULL, 
	mcp_server_id UUID NOT NULL, 
	tool_name VARCHAR(160) NOT NULL, 
	tool_call_id VARCHAR(240), 
	request_payload JSONB NOT NULL, 
	response_payload JSONB, 
	error_payload JSONB, 
	status VARCHAR(32) NOT NULL, 
	claimed_at TIMESTAMP WITHOUT TIME ZONE, 
	cancel_requested_at TIMESTAMP WITH TIME ZONE, 
	completed_at TIMESTAMP WITHOUT TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_self_hosted_mcp_jobs PRIMARY KEY (id), 
	CONSTRAINT uq_self_hosted_mcp_sdk_call UNIQUE (agent_run_id, tool_call_id), 
	CONSTRAINT fk_self_hosted_mcp_jobs_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	CONSTRAINT fk_self_hosted_mcp_jobs_workspace_runtime_id_workspace_runtimes FOREIGN KEY(workspace_runtime_id) REFERENCES workspace_runtimes (id) ON DELETE CASCADE, 
	CONSTRAINT fk_self_hosted_mcp_jobs_worker_id_self_hosted_workers FOREIGN KEY(worker_id) REFERENCES self_hosted_workers (id) ON DELETE SET NULL, 
	CONSTRAINT fk_self_hosted_mcp_jobs_agent_run_id_agent_runs FOREIGN KEY(agent_run_id) REFERENCES agent_runs (id) ON DELETE CASCADE, 
	CONSTRAINT fk_self_hosted_mcp_jobs_mcp_server_id_mcp_servers FOREIGN KEY(mcp_server_id) REFERENCES mcp_servers (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_self_hosted_mcp_jobs_agent_run ON self_hosted_mcp_jobs (agent_run_id)""",
    """CREATE INDEX ix_self_hosted_mcp_jobs_workspace_status ON self_hosted_mcp_jobs (workspace_id, status)""",
    """CREATE INDEX ix_self_hosted_mcp_jobs_workspace_worker ON self_hosted_mcp_jobs (workspace_id, worker_id)""",
    """CREATE TABLE local_file_references (
	workspace_id UUID NOT NULL, 
	workspace_runtime_id UUID NOT NULL, 
	task_id UUID, 
	path VARCHAR(1024) NOT NULL, 
	label VARCHAR(240) NOT NULL, 
	metadata JSONB NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_local_file_references PRIMARY KEY (id), 
	CONSTRAINT fk_local_file_references_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	CONSTRAINT fk_local_file_references_workspace_runtime_id_workspace_c0f3 FOREIGN KEY(workspace_runtime_id) REFERENCES workspace_runtimes (id) ON DELETE CASCADE, 
	CONSTRAINT fk_local_file_references_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE SET NULL
)""",
    """CREATE INDEX ix_local_file_references_workspace_runtime ON local_file_references (workspace_id, workspace_runtime_id)""",
    """CREATE INDEX ix_local_file_references_workspace_task ON local_file_references (workspace_id, task_id)""",
    """CREATE TABLE self_hosted_artifact_uploads (
	workspace_id UUID NOT NULL, 
	worker_id UUID NOT NULL, 
	agent_run_id UUID, 
	filename VARCHAR(260) NOT NULL, 
	storage_key VARCHAR(1024), 
	checksum_sha256 VARCHAR(64), 
	metadata JSONB NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_self_hosted_artifact_uploads PRIMARY KEY (id), 
	CONSTRAINT fk_self_hosted_artifact_uploads_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	CONSTRAINT fk_self_hosted_artifact_uploads_worker_id_self_hosted_workers FOREIGN KEY(worker_id) REFERENCES self_hosted_workers (id) ON DELETE CASCADE, 
	CONSTRAINT fk_self_hosted_artifact_uploads_agent_run_id_agent_runs FOREIGN KEY(agent_run_id) REFERENCES agent_runs (id) ON DELETE SET NULL
)""",
    """CREATE INDEX ix_self_hosted_artifact_uploads_workspace_run ON self_hosted_artifact_uploads (workspace_id, agent_run_id)""",
    """CREATE INDEX ix_self_hosted_artifact_uploads_workspace_status ON self_hosted_artifact_uploads (workspace_id, status)""",
)
