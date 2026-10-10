"""add dynamic capability resources and MCP tool schemas

Revision ID: 0057_dynamic_capabilities
Revises: 0056_user_token_scopes
Create Date: 2026-09-07 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0057_dynamic_capabilities"
down_revision: str | None = "0056_user_token_scopes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_tool_allowlist",
        sa.Column("description", sa.String(length=2_000), server_default="", nullable=False),
    )
    op.add_column(
        "mcp_tool_allowlist",
        sa.Column(
            "input_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_table(
        "capability_resources",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("resource_type", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=2_000), nullable=False),
        sa.Column("access_mode", sa.String(length=32), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parameter_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("default_parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capability_resources")),
        sa.UniqueConstraint(
            "workspace_id",
            "key",
            name="uq_capability_resources_workspace_key",
        ),
    )
    op.create_index(
        "ix_capability_resources_workspace_type",
        "capability_resources",
        ["workspace_id", "resource_type"],
    )
    op.create_index(
        "ix_capability_resources_workspace_status",
        "capability_resources",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_capability_resources_workspace_status",
        table_name="capability_resources",
    )
    op.drop_index(
        "ix_capability_resources_workspace_type",
        table_name="capability_resources",
    )
    op.drop_table("capability_resources")
    op.drop_column("mcp_tool_allowlist", "input_schema")
    op.drop_column("mcp_tool_allowlist", "description")
