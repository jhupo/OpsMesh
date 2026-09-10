"""Add durable task ownership transfer packages."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0073_task_transfers"
down_revision = "0072_release_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "owner_agent_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("owner_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index(
        "ix_tasks_workspace_owner",
        "tasks",
        ["workspace_id", "owner_agent_profile_id"],
    )
    op.execute(
        sa.text(
            """
            UPDATE tasks AS task
            SET owner_agent_profile_id = team.manager_agent_profile_id
            FROM agent_teams AS team
            WHERE task.agent_team_id = team.id
              AND task.owner_agent_profile_id IS NULL
              AND team.manager_agent_profile_id IS NOT NULL
            """
        )
    )
    op.alter_column("tasks", "owner_version", server_default=None)

    op.create_table(
        "task_transfers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_agent_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_agent_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "accepted_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_owner_version", sa.Integer(), nullable=False),
        sa.Column("target_owner_version", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=1_000), nullable=False),
        sa.Column(
            "package",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=1_000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_task_transfers_workspace_idempotency",
        ),
        sa.UniqueConstraint("task_id", "revision", name="uq_task_transfers_task_revision"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'cancelled')",
            name="ck_task_transfers_status",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_task_transfers_revision"),
        sa.CheckConstraint(
            "source_owner_version >= 1",
            name="ck_task_transfers_source_owner_version",
        ),
        sa.CheckConstraint(
            "target_owner_version IS NULL OR target_owner_version >= 1",
            name="ck_task_transfers_target_owner_version",
        ),
    )
    op.create_index(
        "ix_task_transfers_workspace_task",
        "task_transfers",
        ["workspace_id", "task_id"],
    )
    op.create_index(
        "ix_task_transfers_workspace_status",
        "task_transfers",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_task_transfers_workspace_target",
        "task_transfers",
        ["workspace_id", "target_agent_profile_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_transfers_workspace_target", table_name="task_transfers")
    op.drop_index("ix_task_transfers_workspace_status", table_name="task_transfers")
    op.drop_index("ix_task_transfers_workspace_task", table_name="task_transfers")
    op.drop_table("task_transfers")
    op.drop_index("ix_tasks_workspace_owner", table_name="tasks")
    op.drop_column("tasks", "owner_version")
    op.drop_column("tasks", "owner_agent_profile_id")
