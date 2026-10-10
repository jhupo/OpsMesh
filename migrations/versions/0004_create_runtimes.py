"""create runtimes

Revision ID: 0004_create_runtimes
Revises: 0003_create_files_artifacts
Create Date: 2026-05-17 05:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_create_runtimes"
down_revision: str | None = "0003_create_files_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_templates",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("image", sa.String(length=260), nullable=False),
        sa.Column("default_limits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("default_network_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_templates")),
        sa.UniqueConstraint("name", name=op.f("uq_runtime_templates_name")),
    )
    op.create_index("ix_runtime_templates_status", "runtime_templates", ["status"])

    op.create_table(
        "workspace_runtimes",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("runtime_template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("runtime_provider", sa.String(length=32), nullable=False),
        sa.Column("runtime_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("connection_status", sa.String(length=32), nullable=False),
        sa.Column("docker_container_id", sa.String(length=120), nullable=True),
        sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("network_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["runtime_template_id"], ["runtime_templates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_runtimes")),
    )
    op.create_index("ix_workspace_runtimes_container", "workspace_runtimes", ["docker_container_id"])
    op.create_index("ix_workspace_runtimes_workspace_status", "workspace_runtimes", ["workspace_id", "status"])

    op.create_table(
        "runtime_events",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_runtime_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_runtime_id"], ["workspace_runtimes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_events")),
    )
    op.create_index("ix_runtime_events_workspace_runtime", "runtime_events", ["workspace_id", "workspace_runtime_id"])
    op.create_index("ix_runtime_events_workspace_type", "runtime_events", ["workspace_id", "event_type"])

    op.create_table(
        "runtime_commands",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_runtime_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout", sa.String(), nullable=False),
        sa.Column("stderr", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_runtime_id"], ["workspace_runtimes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_commands")),
    )
    op.create_index("ix_runtime_commands_workspace_runtime", "runtime_commands", ["workspace_id", "workspace_runtime_id"])
    op.create_index("ix_runtime_commands_workspace_status", "runtime_commands", ["workspace_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_runtime_commands_workspace_status", table_name="runtime_commands")
    op.drop_index("ix_runtime_commands_workspace_runtime", table_name="runtime_commands")
    op.drop_table("runtime_commands")
    op.drop_index("ix_runtime_events_workspace_type", table_name="runtime_events")
    op.drop_index("ix_runtime_events_workspace_runtime", table_name="runtime_events")
    op.drop_table("runtime_events")
    op.drop_index("ix_workspace_runtimes_workspace_status", table_name="workspace_runtimes")
    op.drop_index("ix_workspace_runtimes_container", table_name="workspace_runtimes")
    op.drop_table("workspace_runtimes")
    op.drop_index("ix_runtime_templates_status", table_name="runtime_templates")
    op.drop_table("runtime_templates")

