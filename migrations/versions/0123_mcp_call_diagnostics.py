"""Index SDK tool call identities for workspace-scoped error investigations."""

import sqlalchemy as sa
from alembic import op

revision = "0123_mcp_call_diagnostics"
down_revision = "0122_native_agent_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mcp_tool_call_logs", sa.Column("tool_call_id", sa.String(160), nullable=True))
    op.create_index(
        "ix_mcp_tool_call_logs_workspace_call",
        "mcp_tool_call_logs",
        ["workspace_id", "tool_call_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_tool_call_logs_workspace_call", table_name="mcp_tool_call_logs")
    op.drop_column("mcp_tool_call_logs", "tool_call_id")
