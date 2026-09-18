"""Persist MCP discovery state and discovered tool metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0093_mcp_discovery_metadata"
down_revision: str | None = "0092_observability_evidence_closure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column("discovery_status", sa.String(length=32), nullable=False, server_default="never"),
    )
    op.add_column(
        "mcp_servers",
        sa.Column("discovery_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("mcp_servers", sa.Column("discovered_at", sa.DateTime(timezone=True)))
    op.add_column("mcp_servers", sa.Column("discovery_checksum", sa.String(length=80)))
    op.add_column("mcp_servers", sa.Column("discovery_error", sa.String()))
    op.alter_column("mcp_servers", "discovery_status", server_default=None)
    op.alter_column("mcp_servers", "discovery_version", server_default=None)

    op.add_column(
        "mcp_tool_allowlist",
        sa.Column("title", sa.String(length=240), nullable=False, server_default=""),
    )
    op.add_column(
        "mcp_tool_allowlist",
        sa.Column("output_schema", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "mcp_tool_allowlist",
        sa.Column(
            "discovery_source", sa.String(length=32), nullable=False, server_default="manual"
        ),
    )
    op.add_column(
        "mcp_tool_allowlist",
        sa.Column(
            "discovery_status", sa.String(length=32), nullable=False, server_default="manual"
        ),
    )
    op.add_column("mcp_tool_allowlist", sa.Column("discovery_checksum", sa.String(length=80)))
    op.add_column("mcp_tool_allowlist", sa.Column("discovered_at", sa.DateTime(timezone=True)))
    op.alter_column("mcp_tool_allowlist", "title", server_default=None)
    op.alter_column("mcp_tool_allowlist", "output_schema", server_default=None)
    op.alter_column("mcp_tool_allowlist", "discovery_source", server_default=None)
    op.alter_column("mcp_tool_allowlist", "discovery_status", server_default=None)


def downgrade() -> None:
    for column in (
        "discovered_at",
        "discovery_checksum",
        "discovery_status",
        "discovery_source",
        "output_schema",
        "title",
    ):
        op.drop_column("mcp_tool_allowlist", column)
    for column in (
        "discovery_error",
        "discovery_checksum",
        "discovered_at",
        "discovery_version",
        "discovery_status",
    ):
        op.drop_column("mcp_servers", column)
