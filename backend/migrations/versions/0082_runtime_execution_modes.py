"""Add selectable isolated, pooled, and persistent runtime execution modes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0082_runtime_execution_modes"
down_revision = "0081_self_hosted_attestation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_runtimes",
        sa.Column(
            "execution_mode",
            sa.String(length=16),
            nullable=False,
            server_default="isolated",
        ),
    )
    op.add_column(
        "workspace_runtimes",
        sa.Column("pool_key", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "workspace_runtimes",
        sa.Column(
            "execution_pool_member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_runtimes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "workspace_runtime_execution_mode_valid",
        "workspace_runtimes",
        "execution_mode in ('none', 'isolated', 'pooled', 'persistent')",
    )
    op.create_index(
        "ix_workspace_runtimes_workspace_pool",
        "workspace_runtimes",
        ["workspace_id", "pool_key", "execution_mode", "status"],
    )
    op.create_index(
        "ix_workspace_runtimes_pool_member",
        "workspace_runtimes",
        ["workspace_id", "execution_pool_member_id"],
    )
    op.alter_column("workspace_runtimes", "execution_mode", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_runtimes_pool_member",
        table_name="workspace_runtimes",
    )
    op.drop_index(
        "ix_workspace_runtimes_workspace_pool",
        table_name="workspace_runtimes",
    )
    op.drop_constraint(
        "workspace_runtime_execution_mode_valid",
        "workspace_runtimes",
        type_="check",
    )
    op.drop_column("workspace_runtimes", "execution_pool_member_id")
    op.drop_column("workspace_runtimes", "pool_key")
    op.drop_column("workspace_runtimes", "execution_mode")
