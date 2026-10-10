"""create runtime spaces

Revision ID: 0025_runtime_spaces
Revises: 0024_skill_install_snapshots
Create Date: 2026-05-19 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_runtime_spaces"
down_revision: str | None = "0024_skill_install_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_spaces",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("default_runtime_template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("network_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("storage_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cleanup_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["default_runtime_template_id"],
            ["runtime_templates.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_spaces")),
    )
    op.create_index(
        "ix_runtime_spaces_workspace_scope",
        "runtime_spaces",
        ["workspace_id", "scope"],
    )
    op.create_index(
        "ix_runtime_spaces_workspace_status",
        "runtime_spaces",
        ["workspace_id", "status"],
    )

    op.create_table(
        "runtime_space_bindings",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("runtime_space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["runtime_space_id"],
            ["runtime_spaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_space_bindings")),
        sa.UniqueConstraint(
            "workspace_id",
            "target_type",
            "target_id",
            name="uq_runtime_space_bindings_target",
        ),
    )
    op.create_index(
        "ix_runtime_space_bindings_space",
        "runtime_space_bindings",
        ["workspace_id", "runtime_space_id"],
    )

    op.create_table(
        "runtime_space_quotas",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("runtime_space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quota_key", sa.String(length=80), nullable=False),
        sa.Column("limit_value", sa.Integer(), nullable=False),
        sa.Column("reserved_value", sa.Integer(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["runtime_space_id"],
            ["runtime_spaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_space_quotas")),
        sa.UniqueConstraint(
            "runtime_space_id",
            "quota_key",
            name="uq_runtime_space_quotas_space_key",
        ),
    )
    op.create_index(
        "ix_runtime_space_quotas_workspace_space",
        "runtime_space_quotas",
        ["workspace_id", "runtime_space_id"],
    )

    op.create_table(
        "runtime_space_reservations",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("runtime_space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_step_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reservation_key", sa.String(length=180), nullable=False),
        sa.Column("resource_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["runtime_space_id"],
            ["runtime_spaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_step_id"], ["task_steps.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_space_reservations")),
        sa.UniqueConstraint(
            "runtime_space_id",
            "reservation_key",
            name="uq_runtime_space_reservations_space_key",
        ),
    )
    op.create_index(
        "ix_runtime_space_reservations_space_status",
        "runtime_space_reservations",
        ["runtime_space_id", "status"],
    )
    op.create_index(
        "ix_runtime_space_reservations_workspace_status",
        "runtime_space_reservations",
        ["workspace_id", "status"],
    )

    op.create_table(
        "runtime_space_events",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("runtime_space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["runtime_space_id"],
            ["runtime_spaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_space_events")),
    )
    op.create_index(
        "ix_runtime_space_events_space",
        "runtime_space_events",
        ["workspace_id", "runtime_space_id"],
    )
    op.create_index(
        "ix_runtime_space_events_workspace_type",
        "runtime_space_events",
        ["workspace_id", "event_type"],
    )

    for table_name in (
        "agent_teams",
        "tasks",
        "task_steps",
        "agent_runs",
        "workspace_runtimes",
        "runtime_events",
        "runtime_commands",
    ):
        op.add_column(
            table_name,
            sa.Column("runtime_space_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            op.f(f"fk_{table_name}_runtime_space_id_runtime_spaces"),
            table_name,
            "runtime_spaces",
            ["runtime_space_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index(
        "ix_agent_teams_workspace_runtime_space",
        "agent_teams",
        ["workspace_id", "runtime_space_id"],
    )
    op.create_index(
        "ix_tasks_workspace_runtime_space",
        "tasks",
        ["workspace_id", "runtime_space_id"],
    )
    op.create_index(
        "ix_task_steps_workspace_runtime_space",
        "task_steps",
        ["workspace_id", "runtime_space_id"],
    )
    op.create_index(
        "ix_agent_runs_workspace_runtime_space",
        "agent_runs",
        ["workspace_id", "runtime_space_id"],
    )
    op.create_index(
        "ix_workspace_runtimes_workspace_runtime_space",
        "workspace_runtimes",
        ["workspace_id", "runtime_space_id"],
    )
    op.create_index(
        "ix_runtime_events_workspace_runtime_space",
        "runtime_events",
        ["workspace_id", "runtime_space_id"],
    )
    op.create_index(
        "ix_runtime_commands_workspace_runtime_space",
        "runtime_commands",
        ["workspace_id", "runtime_space_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_runtimes_workspace_runtime_space",
        table_name="workspace_runtimes",
    )
    op.drop_index(
        "ix_runtime_commands_workspace_runtime_space",
        table_name="runtime_commands",
    )
    op.drop_index(
        "ix_runtime_events_workspace_runtime_space",
        table_name="runtime_events",
    )
    op.drop_index("ix_agent_runs_workspace_runtime_space", table_name="agent_runs")
    op.drop_index("ix_task_steps_workspace_runtime_space", table_name="task_steps")
    op.drop_index("ix_tasks_workspace_runtime_space", table_name="tasks")
    op.drop_index("ix_agent_teams_workspace_runtime_space", table_name="agent_teams")

    for table_name in (
        "runtime_commands",
        "runtime_events",
        "workspace_runtimes",
        "agent_runs",
        "task_steps",
        "tasks",
        "agent_teams",
    ):
        op.drop_constraint(
            op.f(f"fk_{table_name}_runtime_space_id_runtime_spaces"),
            table_name,
            type_="foreignkey",
        )
        op.drop_column(table_name, "runtime_space_id")

    op.drop_index("ix_runtime_space_events_workspace_type", table_name="runtime_space_events")
    op.drop_index("ix_runtime_space_events_space", table_name="runtime_space_events")
    op.drop_table("runtime_space_events")

    op.drop_index(
        "ix_runtime_space_reservations_workspace_status",
        table_name="runtime_space_reservations",
    )
    op.drop_index(
        "ix_runtime_space_reservations_space_status",
        table_name="runtime_space_reservations",
    )
    op.drop_table("runtime_space_reservations")

    op.drop_index("ix_runtime_space_quotas_workspace_space", table_name="runtime_space_quotas")
    op.drop_table("runtime_space_quotas")

    op.drop_index("ix_runtime_space_bindings_space", table_name="runtime_space_bindings")
    op.drop_table("runtime_space_bindings")

    op.drop_index("ix_runtime_spaces_workspace_status", table_name="runtime_spaces")
    op.drop_index("ix_runtime_spaces_workspace_scope", table_name="runtime_spaces")
    op.drop_table("runtime_spaces")
