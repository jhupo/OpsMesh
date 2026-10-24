"""Persist logical Agent instances separately from definitions and runs."""

import sqlalchemy as sa
from alembic import op

revision = "0002_execution_instances"
down_revision = "0001_v4_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_instances",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("owner_user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("agent_id", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("environment_id", sa.UUID(), sa.ForeignKey("sandbox_environments.id")),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("encrypted_config", sa.Text(), nullable=False),
        sa.Column("key_id", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("owner_user_id", "request_key"),
    )
    op.create_index(
        "ix_execution_instances_owner_user_id", "execution_instances", ["owner_user_id"]
    )
    for table in ("execution_runs", "execution_sessions"):
        op.add_column(table, sa.Column("instance_id", sa.UUID(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_instance_id_execution_instances",
            table,
            "execution_instances",
            ["instance_id"],
            ["id"],
        )
        op.create_index(f"ix_{table}_instance_id", table, ["instance_id"])
        op.alter_column(table, "revision_id", existing_type=sa.UUID(), nullable=True)
    op.drop_constraint(
        "fk_execution_sessions_revision_id_agent_revisions",
        "execution_sessions",
        type_="foreignkey",
    )
    # Existing revisions cannot be silently converted into authorized snapshots.
    # Keep their records and identities available to the old workflow adapter.
    op.add_column(
        "execution_runs",
        sa.Column("driver", sa.String(24), nullable=False, server_default="legacy"),
    )
    op.alter_column("execution_runs", "driver", server_default=None)
    op.add_column("execution_runs", sa.Column("attempt_id", sa.String(100), nullable=True))
    op.create_table(
        "execution_events",
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("execution_runs.id"), primary_key=True),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("key_id", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    raise RuntimeError("Execution snapshots require an explicit data migration before downgrade")
