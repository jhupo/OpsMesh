"""enhance mcp tool call logs

Revision ID: 0034_enhance_mcp_tool_call_logs
Revises: 0033_workspace_memory_entries
Create Date: 2026-05-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034_enhance_mcp_tool_call_logs"
down_revision: str | None = "0033_workspace_memory_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_tool_call_logs",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "mcp_tool_call_logs",
        sa.Column("task_step_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "mcp_tool_call_logs",
        sa.Column("agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("mcp_tool_call_logs", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column(
        "mcp_tool_call_logs",
        sa.Column("argument_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "mcp_tool_call_logs",
        sa.Column("response_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "mcp_tool_call_logs",
        sa.Column("error_code", sa.String(length=120), nullable=True),
    )
    op.create_foreign_key(
        "fk_mcp_tool_call_logs_task_id_tasks",
        "mcp_tool_call_logs",
        "tasks",
        ["task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_mcp_tool_call_logs_task_step_id_task_steps",
        "mcp_tool_call_logs",
        "task_steps",
        ["task_step_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_mcp_tool_call_logs_agent_profile_id_agent_profiles",
        "mcp_tool_call_logs",
        "agent_profiles",
        ["agent_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_mcp_tool_call_logs_workspace_agent",
        "mcp_tool_call_logs",
        ["workspace_id", "agent_profile_id"],
    )
    op.create_index(
        "ix_mcp_tool_call_logs_workspace_status",
        "mcp_tool_call_logs",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_tool_call_logs_workspace_status", table_name="mcp_tool_call_logs")
    op.drop_index("ix_mcp_tool_call_logs_workspace_agent", table_name="mcp_tool_call_logs")
    op.drop_constraint(
        "fk_mcp_tool_call_logs_agent_profile_id_agent_profiles",
        "mcp_tool_call_logs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_mcp_tool_call_logs_task_step_id_task_steps",
        "mcp_tool_call_logs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_mcp_tool_call_logs_task_id_tasks",
        "mcp_tool_call_logs",
        type_="foreignkey",
    )
    op.drop_column("mcp_tool_call_logs", "error_code")
    op.drop_column("mcp_tool_call_logs", "response_sha256")
    op.drop_column("mcp_tool_call_logs", "argument_sha256")
    op.drop_column("mcp_tool_call_logs", "latency_ms")
    op.drop_column("mcp_tool_call_logs", "agent_profile_id")
    op.drop_column("mcp_tool_call_logs", "task_step_id")
    op.drop_column("mcp_tool_call_logs", "task_id")
