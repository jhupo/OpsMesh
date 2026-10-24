"""Initial schema for the new plugin host. Requires a separate database."""

from alembic import op

revision = "20261024_host"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE audit_events (
	user_id VARCHAR(36),
	method VARCHAR(10) NOT NULL,
	route VARCHAR(200) NOT NULL,
	status INTEGER NOT NULL,
	request_id VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)""")
    op.execute("""CREATE TABLE groups (
	name VARCHAR(100) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
)""")
    op.execute("""CREATE TABLE users (
	name VARCHAR(100) NOT NULL,
	enabled BOOLEAN NOT NULL,
	roles JSON NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
)""")
    op.execute("""CREATE TABLE access_tokens (
	user_id VARCHAR(36) NOT NULL,
	digest VARCHAR(64) NOT NULL,
	label VARCHAR(100) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE,
	revoked BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	UNIQUE (digest)
)""")
    op.execute("""CREATE TABLE artifacts (
	owner_id VARCHAR(36) NOT NULL,
	task_id VARCHAR(36) NOT NULL,
	name VARCHAR(200) NOT NULL,
	size INTEGER NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)""")
    op.execute("""CREATE TABLE group_members (
	group_id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (group_id, user_id),
	FOREIGN KEY(group_id) REFERENCES groups (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)""")
    op.execute("""CREATE TABLE model_connections (
	owner_id VARCHAR(36) NOT NULL,
	name VARCHAR(100) NOT NULL,
	base_url VARCHAR(2000) NOT NULL,
	api_mode VARCHAR(30) NOT NULL,
	encrypted_key TEXT NOT NULL,
	enabled BOOLEAN NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, name),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)""")
    op.execute("""CREATE TABLE plugins (
	owner_id VARCHAR(36) NOT NULL,
	name VARCHAR(100) NOT NULL,
	version VARCHAR(60) NOT NULL,
	digest VARCHAR(64) NOT NULL,
	manifest JSON NOT NULL,
	enabled BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, name, version),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)""")
    op.execute("""CREATE TABLE quotas (
	user_id VARCHAR(36) NOT NULL,
	monthly_tokens BIGINT NOT NULL,
	storage_bytes BIGINT NOT NULL,
	concurrent_tasks INTEGER NOT NULL,
	PRIMARY KEY (user_id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)""")
    op.execute("""CREATE TABLE token_budgets (
	user_id VARCHAR(36) NOT NULL,
	period VARCHAR(7) NOT NULL,
	consumed BIGINT NOT NULL,
	reserved BIGINT NOT NULL,
	PRIMARY KEY (user_id, period),
	FOREIGN KEY(user_id) REFERENCES users (id)
)""")
    op.execute("""CREATE TABLE token_reservations (
	user_id VARCHAR(36) NOT NULL,
	task_id VARCHAR(36) NOT NULL,
	period VARCHAR(7) NOT NULL,
	amount BIGINT NOT NULL,
	actual BIGINT,
	status VARCHAR(20) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)""")
    op.execute("""CREATE TABLE workspaces (
	owner_id VARCHAR(36) NOT NULL,
	name VARCHAR(100) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)""")
    op.execute("""CREATE TABLE agent_instances (
	owner_id VARCHAR(36) NOT NULL,
	name VARCHAR(100) NOT NULL,
	plugin_id VARCHAR(36) NOT NULL,
	entry VARCHAR(64) NOT NULL,
	encrypted_config TEXT NOT NULL,
	dependencies JSON NOT NULL,
	workspace_id VARCHAR(36),
	enabled BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	FOREIGN KEY(plugin_id) REFERENCES plugins (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)""")
    op.execute("""CREATE TABLE publications (
	owner_id VARCHAR(36) NOT NULL,
	name VARCHAR(100) NOT NULL,
	version VARCHAR(60) NOT NULL,
	kind VARCHAR(20) NOT NULL,
	plugin_id VARCHAR(36) NOT NULL,
	contribution VARCHAR(64) NOT NULL,
	description TEXT NOT NULL,
	visibility VARCHAR(20) NOT NULL,
	group_ids JSON NOT NULL,
	encrypted_config TEXT NOT NULL,
	dependencies JSON NOT NULL,
	enabled BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, name, version),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	FOREIGN KEY(plugin_id) REFERENCES plugins (id)
)""")
    op.execute("""CREATE TABLE channels (
	owner_id VARCHAR(36) NOT NULL,
	name VARCHAR(100) NOT NULL,
	adapter VARCHAR(100) NOT NULL,
	plugin_id VARCHAR(36),
	agent_id VARCHAR(36) NOT NULL,
	encrypted_config TEXT NOT NULL,
	enabled BOOLEAN NOT NULL,
	revision INTEGER NOT NULL,
	worker_id VARCHAR(100),
	lease_until TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	FOREIGN KEY(plugin_id) REFERENCES plugins (id),
	FOREIGN KEY(agent_id) REFERENCES agent_instances (id)
)""")
    op.execute("""CREATE TABLE schedules (
	owner_id VARCHAR(36) NOT NULL,
	agent_id VARCHAR(36) NOT NULL,
	name VARCHAR(100) NOT NULL,
	kind VARCHAR(20) NOT NULL,
	expression VARCHAR(100),
	interval_seconds INTEGER,
	timezone VARCHAR(100) NOT NULL,
	next_at TIMESTAMP WITH TIME ZONE NOT NULL,
	encrypted_input TEXT NOT NULL,
	enabled BOOLEAN NOT NULL,
	last_task_id VARCHAR(36),
	error_code VARCHAR(100),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	FOREIGN KEY(agent_id) REFERENCES agent_instances (id)
)""")
    op.execute("""CREATE TABLE tasks (
	owner_id VARCHAR(36) NOT NULL,
	agent_id VARCHAR(36),
	parent_id VARCHAR(36),
	schedule_id VARCHAR(36),
	status VARCHAR(24) NOT NULL,
	snapshot JSON NOT NULL,
	encrypted_input TEXT NOT NULL,
	encrypted_result TEXT,
	encrypted_source TEXT NOT NULL,
	idempotency_key VARCHAR(200),
	attempt_count INTEGER NOT NULL,
	timeout_seconds INTEGER NOT NULL,
	cancel_requested BOOLEAN NOT NULL,
	checkpoint_id VARCHAR(36),
	decision_id VARCHAR(36),
	error_code VARCHAR(100),
	finished_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, idempotency_key),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	FOREIGN KEY(agent_id) REFERENCES agent_instances (id),
	FOREIGN KEY(parent_id) REFERENCES tasks (id)
)""")
    op.execute("""CREATE TABLE attempts (
	task_id VARCHAR(36) NOT NULL,
	number INTEGER NOT NULL,
	worker_id VARCHAR(100) NOT NULL,
	trace_id VARCHAR(100) NOT NULL,
	status VARCHAR(24) NOT NULL,
	lease_until TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(task_id) REFERENCES tasks (id)
)""")
    op.execute("""CREATE TABLE channel_identities (
	channel_id VARCHAR(36) NOT NULL,
	external_user_id VARCHAR(200) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (channel_id, external_user_id),
	FOREIGN KEY(channel_id) REFERENCES channels (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)""")
    op.execute("""CREATE TABLE deliveries (
	channel_id VARCHAR(36) NOT NULL,
	task_id VARCHAR(36) NOT NULL,
	deduplication_key VARCHAR(100) NOT NULL,
	encrypted_message TEXT NOT NULL,
	status VARCHAR(24) NOT NULL,
	attempts INTEGER NOT NULL,
	error_code VARCHAR(100),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (task_id, deduplication_key),
	FOREIGN KEY(channel_id) REFERENCES channels (id),
	FOREIGN KEY(task_id) REFERENCES tasks (id)
)""")
    op.execute("""CREATE TABLE inbox (
	channel_id VARCHAR(36) NOT NULL,
	external_message_id VARCHAR(200) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	task_id VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (channel_id, external_message_id),
	FOREIGN KEY(channel_id) REFERENCES channels (id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	FOREIGN KEY(task_id) REFERENCES tasks (id)
)""")
    op.execute("""CREATE TABLE checkpoints (
	task_id VARCHAR(36) NOT NULL,
	attempt_id VARCHAR(36) NOT NULL,
	sdk_version VARCHAR(40) NOT NULL,
	encrypted_state TEXT NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(task_id) REFERENCES tasks (id),
	FOREIGN KEY(attempt_id) REFERENCES attempts (id)
)""")
    op.execute("""CREATE TABLE interactions (
	task_id VARCHAR(36) NOT NULL,
	checkpoint_id VARCHAR(36) NOT NULL,
	recipient_id VARCHAR(36) NOT NULL,
	kind VARCHAR(20) NOT NULL,
	encrypted_prompt TEXT NOT NULL,
	encrypted_decision TEXT,
	responder_id VARCHAR(36),
	status VARCHAR(24) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(task_id) REFERENCES tasks (id),
	FOREIGN KEY(checkpoint_id) REFERENCES checkpoints (id),
	FOREIGN KEY(recipient_id) REFERENCES users (id),
	FOREIGN KEY(responder_id) REFERENCES users (id)
)""")
    op.execute("""CREATE INDEX ix_audit_events_request_id ON audit_events (request_id)""")
    op.execute("""CREATE INDEX ix_audit_events_user_id ON audit_events (user_id)""")
    op.execute("""CREATE INDEX ix_access_tokens_user_id ON access_tokens (user_id)""")
    op.execute("""CREATE INDEX ix_artifacts_owner_id ON artifacts (owner_id)""")
    op.execute("""CREATE INDEX ix_artifacts_task_id ON artifacts (task_id)""")
    op.execute("""CREATE INDEX ix_model_connections_owner_id ON model_connections (owner_id)""")
    op.execute("""CREATE INDEX ix_plugins_owner_id ON plugins (owner_id)""")
    op.execute("""CREATE INDEX ix_token_reservations_task_id ON token_reservations (task_id)""")
    op.execute("""CREATE INDEX ix_token_reservations_user_id ON token_reservations (user_id)""")
    op.execute("""CREATE INDEX ix_workspaces_owner_id ON workspaces (owner_id)""")
    op.execute("""CREATE INDEX ix_agent_instances_owner_id ON agent_instances (owner_id)""")
    op.execute("""CREATE INDEX ix_publications_owner_id ON publications (owner_id)""")
    op.execute("""CREATE INDEX ix_schedules_next_at ON schedules (next_at)""")
    op.execute("""CREATE INDEX ix_schedules_owner_id ON schedules (owner_id)""")
    op.execute("""CREATE INDEX ix_tasks_owner_id ON tasks (owner_id)""")
    op.execute("""CREATE INDEX ix_tasks_parent_id ON tasks (parent_id)""")
    op.execute("""CREATE INDEX ix_tasks_schedule_id ON tasks (schedule_id)""")
    op.execute("""CREATE INDEX ix_tasks_status ON tasks (status)""")
    op.execute("""CREATE INDEX ix_attempts_lease_until ON attempts (lease_until)""")
    op.execute("""CREATE INDEX ix_attempts_task_id ON attempts (task_id)""")
    op.execute("""CREATE INDEX ix_attempts_trace_id ON attempts (trace_id)""")
    op.execute("""CREATE INDEX ix_deliveries_channel_id ON deliveries (channel_id)""")
    op.execute("""CREATE INDEX ix_deliveries_status ON deliveries (status)""")
    op.execute("""CREATE INDEX ix_checkpoints_task_id ON checkpoints (task_id)""")
    op.execute("""CREATE INDEX ix_interactions_recipient_id ON interactions (recipient_id)""")
    op.execute("""CREATE INDEX ix_interactions_task_id ON interactions (task_id)""")


def downgrade() -> None:
    raise RuntimeError("Automatic destructive downgrade is disabled; restore a database backup")
