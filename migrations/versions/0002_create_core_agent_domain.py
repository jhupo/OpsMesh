"""create core agent domain

Revision ID: 0002_create_core_agent_domain
Revises: 0001_create_users_workspaces
Create Date: 2026-05-17 03:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_create_core_agent_domain"
down_revision: str | None = "0001_create_users_workspaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_profiles",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("instructions", sa.String(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("model_settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tool_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("runtime_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("memory_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approval_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_profiles")),
    )
    op.create_index("ix_agent_profiles_workspace_role", "agent_profiles", ["workspace_id", "role"])
    op.create_index("ix_agent_profiles_workspace_status", "agent_profiles", ["workspace_id", "status"])

    op.create_table(
        "agent_teams",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("team_type", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("manager_agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("coordination_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("default_task_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["manager_agent_profile_id"], ["agent_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_teams")),
    )
    op.create_index("ix_agent_teams_workspace_status", "agent_teams", ["workspace_id", "status"])
    op.create_index("ix_agent_teams_workspace_type", "agent_teams", ["workspace_id", "team_type"])

    op.create_table(
        "agent_team_members",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_role", sa.String(length=80), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_team_id"], ["agent_teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_team_members")),
        sa.UniqueConstraint("agent_team_id", "agent_profile_id", name="uq_agent_team_members_team_agent"),
    )
    op.create_index("ix_agent_team_members_workspace_team", "agent_team_members", ["workspace_id", "agent_team_id"])

    op.create_table(
        "tasks",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("domain_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generic_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("domain_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("final_output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_team_id"], ["agent_teams.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
    )
    op.create_index("ix_tasks_workspace_domain", "tasks", ["workspace_id", "domain_type"])
    op.create_index("ix_tasks_workspace_status", "tasks", ["workspace_id", "status"])
    op.create_index("ix_tasks_workspace_team", "tasks", ["workspace_id", "agent_team_id"])

    op.create_table(
        "task_steps",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("dependencies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_summary", sa.String(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["assigned_agent_profile_id"], ["agent_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_steps")),
    )
    op.create_index("ix_task_steps_workspace_status", "task_steps", ["workspace_id", "status"])
    op.create_index("ix_task_steps_workspace_task", "task_steps", ["workspace_id", "task_id"])

    op.create_table(
        "agent_runs",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_step_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("runtime_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_step_id"], ["task_steps.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index("ix_agent_runs_workspace_status", "agent_runs", ["workspace_id", "status"])
    op.create_index("ix_agent_runs_workspace_task", "agent_runs", ["workspace_id", "task_id"])

    op.create_foreign_key(
        "fk_tasks_created_by_agent_run_id_agent_runs",
        "tasks",
        "agent_runs",
        ["created_by_agent_run_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "run_events",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_events")),
        sa.UniqueConstraint("agent_run_id", "sequence", name="uq_run_events_run_sequence"),
    )
    op.create_index("ix_run_events_workspace_run", "run_events", ["workspace_id", "agent_run_id"])
    op.create_index("ix_run_events_workspace_type", "run_events", ["workspace_id", "event_type"])

    op.create_table(
        "audit_events",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=120), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("target_type", sa.String(length=120), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index("ix_audit_events_workspace_action", "audit_events", ["workspace_id", "action"])
    op.create_index("ix_audit_events_workspace_target", "audit_events", ["workspace_id", "target_type", "target_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_workspace_target", table_name="audit_events")
    op.drop_index("ix_audit_events_workspace_action", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_run_events_workspace_type", table_name="run_events")
    op.drop_index("ix_run_events_workspace_run", table_name="run_events")
    op.drop_table("run_events")
    op.drop_constraint("fk_tasks_created_by_agent_run_id_agent_runs", "tasks", type_="foreignkey")
    op.drop_index("ix_agent_runs_workspace_task", table_name="agent_runs")
    op.drop_index("ix_agent_runs_workspace_status", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_task_steps_workspace_task", table_name="task_steps")
    op.drop_index("ix_task_steps_workspace_status", table_name="task_steps")
    op.drop_table("task_steps")
    op.drop_index("ix_tasks_workspace_team", table_name="tasks")
    op.drop_index("ix_tasks_workspace_status", table_name="tasks")
    op.drop_index("ix_tasks_workspace_domain", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_agent_team_members_workspace_team", table_name="agent_team_members")
    op.drop_table("agent_team_members")
    op.drop_index("ix_agent_teams_workspace_type", table_name="agent_teams")
    op.drop_index("ix_agent_teams_workspace_status", table_name="agent_teams")
    op.drop_table("agent_teams")
    op.drop_index("ix_agent_profiles_workspace_status", table_name="agent_profiles")
    op.drop_index("ix_agent_profiles_workspace_role", table_name="agent_profiles")
    op.drop_table("agent_profiles")

