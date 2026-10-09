"""Persist managed MCP deployments and queued lifecycle intent."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0111_managed_mcp"
down_revision = "0110_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_deployments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "mcp_server_id",
            sa.Uuid(),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("runtime_id", sa.Uuid(), sa.ForeignKey("workspace_runtimes.id")),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("runtime_templates.id"), nullable=False),
        sa.Column("network_disabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("execution_identity", postgresql.JSONB(), nullable=False),
        sa.Column("last_error", sa.String(120)),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("server_version", sa.Integer()),
        sa.Column("credential_version", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mcp_deployments_workspace_id", "mcp_deployments", ["workspace_id"])


def downgrade() -> None:
    op.drop_table("mcp_deployments")
