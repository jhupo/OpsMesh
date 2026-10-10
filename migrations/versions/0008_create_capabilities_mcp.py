"""create capabilities mcp

Revision ID: 0008_capabilities_mcp
Revises: 0007_domain_tasks
Create Date: 2026-05-17 16:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_capabilities_mcp"
down_revision: str | None = "0007_domain_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capabilities",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("default_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capabilities")),
        sa.UniqueConstraint("key", name="uq_capabilities_key"),
    )
    op.create_index("ix_capabilities_category", "capabilities", ["category"])

    op.create_table(
        "skills",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("capability_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skills")),
        sa.UniqueConstraint("key", "version", name="uq_skills_key_version"),
    )
    op.create_index("ix_skills_status", "skills", ["status"])

    op.create_table(
        "tool_groups",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("tool_names", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_groups")),
        sa.UniqueConstraint("key", name="uq_tool_groups_key"),
    )
    op.create_index("ix_tool_groups_status", "tool_groups", ["status"])

    op.create_table(
        "workspace_skill_installs",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["installed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_skill_installs")),
        sa.UniqueConstraint("workspace_id", "skill_id", name="uq_workspace_skill_installs_skill"),
    )
    op.create_index(
        "ix_workspace_skill_installs_workspace_status",
        "workspace_skill_installs",
        ["workspace_id", "status"],
    )

    op.create_table(
        "mcp_servers",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("server_type", sa.String(length=80), nullable=False),
        sa.Column("connection", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("health_status", sa.String(length=32), nullable=False),
        sa.Column("last_health_check_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_servers")),
        sa.UniqueConstraint("workspace_id", "name", name="uq_mcp_servers_workspace_name"),
    )
    op.create_index("ix_mcp_servers_workspace_status", "mcp_servers", ["workspace_id", "status"])

    op.create_table(
        "mcp_tool_allowlist",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mcp_server_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(length=160), nullable=False),
        sa.Column("capability_key", sa.String(length=120), nullable=True),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["mcp_server_id"], ["mcp_servers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_tool_allowlist")),
        sa.UniqueConstraint("mcp_server_id", "tool_name", name="uq_mcp_tool_allowlist_tool"),
    )
    op.create_index(
        "ix_mcp_tool_allowlist_workspace_server",
        "mcp_tool_allowlist",
        ["workspace_id", "mcp_server_id"],
    )

    op.create_table(
        "mcp_credential_references",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mcp_server_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("external_ref", sa.String(length=512), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["mcp_server_id"], ["mcp_servers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_credential_references")),
        sa.UniqueConstraint("workspace_id", "name", name="uq_mcp_credential_refs_workspace_name"),
    )
    op.create_index(
        "ix_mcp_credential_refs_workspace_status",
        "mcp_credential_references",
        ["workspace_id", "status"],
    )

    op.create_table(
        "mcp_tool_call_logs",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mcp_server_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["mcp_server_id"], ["mcp_servers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_tool_call_logs")),
    )
    op.create_index(
        "ix_mcp_tool_call_logs_workspace_run",
        "mcp_tool_call_logs",
        ["workspace_id", "agent_run_id"],
    )
    op.create_index(
        "ix_mcp_tool_call_logs_workspace_tool",
        "mcp_tool_call_logs",
        ["workspace_id", "tool_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_tool_call_logs_workspace_tool", table_name="mcp_tool_call_logs")
    op.drop_index("ix_mcp_tool_call_logs_workspace_run", table_name="mcp_tool_call_logs")
    op.drop_table("mcp_tool_call_logs")
    op.drop_index(
        "ix_mcp_credential_refs_workspace_status",
        table_name="mcp_credential_references",
    )
    op.drop_table("mcp_credential_references")
    op.drop_index("ix_mcp_tool_allowlist_workspace_server", table_name="mcp_tool_allowlist")
    op.drop_table("mcp_tool_allowlist")
    op.drop_index("ix_mcp_servers_workspace_status", table_name="mcp_servers")
    op.drop_table("mcp_servers")
    op.drop_index(
        "ix_workspace_skill_installs_workspace_status",
        table_name="workspace_skill_installs",
    )
    op.drop_table("workspace_skill_installs")
    op.drop_index("ix_tool_groups_status", table_name="tool_groups")
    op.drop_table("tool_groups")
    op.drop_index("ix_skills_status", table_name="skills")
    op.drop_table("skills")
    op.drop_index("ix_capabilities_category", table_name="capabilities")
    op.drop_table("capabilities")
