"""Persist conversation sessions, password login and model defaults."""

import sqlalchemy as sa
from alembic import op

revision = "20261024_sessions"
down_revision = "20261024_host"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE login_throttles (
	key VARCHAR(64) NOT NULL,
	attempts INTEGER NOT NULL,
	window_until TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (key)
)""")
    op.execute("""CREATE TABLE agent_sessions (
	owner_id VARCHAR(36) NOT NULL,
	caller_id VARCHAR(36),
	agent_key VARCHAR(36) NOT NULL,
	binding_key VARCHAR(64) NOT NULL,
	scope VARCHAR(20) NOT NULL,
	channel_id VARCHAR(36),
	conversation_id VARCHAR(200),
	active_task_id VARCHAR(36),
	revision INTEGER NOT NULL,
	closed BOOLEAN NOT NULL,
	sandbox_backend VARCHAR(100),
	sandbox_sdk_version VARCHAR(40),
	sandbox_task_id VARCHAR(36),
	sandbox_dirty BOOLEAN NOT NULL,
	encrypted_sandbox_state TEXT,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	FOREIGN KEY(caller_id) REFERENCES users (id),
	UNIQUE (binding_key)
)""")
    op.execute("""CREATE INDEX ix_agent_sessions_agent_key ON agent_sessions (agent_key)""")
    op.execute("""CREATE INDEX ix_agent_sessions_caller_id ON agent_sessions (caller_id)""")
    op.execute("""CREATE INDEX ix_agent_sessions_owner_id ON agent_sessions (owner_id)""")
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=True))
    op.add_column("model_connections", sa.Column("model_name", sa.String(200), nullable=True))
    op.add_column(
        "model_connections",
        sa.Column("reasoning_effort", sa.String(20), nullable=False, server_default="medium"),
    )
    op.add_column(
        "model_connections",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "uq_model_connections_default",
        "model_connections",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.add_column("tasks", sa.Column("session_id", sa.String(36), nullable=True))
    op.add_column("tasks", sa.Column("session_revision", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_tasks_session_id", "tasks", "agent_sessions", ["session_id"], ["id"])
    op.create_index("ix_tasks_session_id", "tasks", ["session_id"])
    op.execute("""
        INSERT INTO agent_sessions (
            id, created_at, owner_id, caller_id, agent_key, binding_key, scope,
            active_task_id, revision, closed, sandbox_dirty
        )
        SELECT id, created_at, COALESCE(snapshot->>'owner_id', owner_id), owner_id,
            snapshot->>'id', 'task:' || id, 'private',
            CASE WHEN status IN ('running', 'waiting_input') THEN id ELSE NULL END,
            CASE WHEN attempt_count > 0 THEN 1 ELSE 0 END, false, false
        FROM tasks
    """)
    op.execute("""
        UPDATE tasks SET session_id = id,
            session_revision = CASE WHEN attempt_count > 0 THEN 1 ELSE NULL END
    """)


def downgrade() -> None:
    raise RuntimeError("Automatic destructive downgrade is disabled; restore a database backup")
