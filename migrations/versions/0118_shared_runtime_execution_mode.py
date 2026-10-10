"""Replace pooled and persistent Runtime modes with shared execution."""

import sqlalchemy as sa
from alembic import op

revision = "0118_shared_runtime_execution_mode"
down_revision = "0117_shared_runtime_hosts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE workspace_runtimes
        SET execution_mode = 'shared'
        WHERE execution_mode IN ('pooled', 'persistent', 'none')
        """
    )
    op.drop_constraint(
        "workspace_runtime_execution_mode_valid",
        "workspace_runtimes",
        type_="check",
    )
    op.create_check_constraint(
        "workspace_runtime_execution_mode_valid",
        "workspace_runtimes",
        "execution_mode in ('isolated', 'shared')",
    )
    op.drop_index("ix_workspace_runtimes_workspace_pool", table_name="workspace_runtimes")
    op.drop_column("workspace_runtimes", "pool_key")


def downgrade() -> None:
    op.add_column(
        "workspace_runtimes",
        sa.Column("pool_key", sa.String(length=160), nullable=True),
    )
    op.create_index(
        "ix_workspace_runtimes_workspace_pool",
        "workspace_runtimes",
        ["workspace_id", "pool_key", "execution_mode", "status"],
    )
    op.drop_constraint(
        "workspace_runtime_execution_mode_valid",
        "workspace_runtimes",
        type_="check",
    )
    op.execute("UPDATE workspace_runtimes SET execution_mode = 'pooled' WHERE execution_mode = 'shared'")
    op.create_check_constraint(
        "workspace_runtime_execution_mode_valid",
        "workspace_runtimes",
        "execution_mode in ('none', 'isolated', 'pooled', 'persistent')",
    )
