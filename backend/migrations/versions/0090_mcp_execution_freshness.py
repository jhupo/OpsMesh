"""Freeze MCP execution configuration and trace correlation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0090_mcp_execution_freshness"
down_revision: str | None = "0089_track_consumed_tool_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table_name in (
        "mcp_servers",
        "mcp_tool_allowlist",
        "mcp_credential_references",
    ):
        op.add_column(
            table_name,
            sa.Column(
                "configuration_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )
        op.alter_column(table_name, "configuration_version", server_default=None)

    op.add_column(
        "mcp_tool_call_logs",
        sa.Column("trace_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "mcp_tool_call_logs",
        sa.Column("span_id", sa.String(length=16), nullable=True),
    )
    op.create_index(
        "ix_mcp_tool_call_logs_workspace_trace",
        "mcp_tool_call_logs",
        ["workspace_id", "trace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mcp_tool_call_logs_workspace_trace",
        table_name="mcp_tool_call_logs",
    )
    op.drop_column("mcp_tool_call_logs", "span_id")
    op.drop_column("mcp_tool_call_logs", "trace_id")
    for table_name in (
        "mcp_credential_references",
        "mcp_tool_allowlist",
        "mcp_servers",
    ):
        op.drop_column(table_name, "configuration_version")
