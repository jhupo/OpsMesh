"""Expose non-executing storage and cleanup data as descriptive metadata."""

from alembic import op

revision = "0144_runtime_space_metadata"
down_revision = "0143_execution_release_boundary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("runtime_spaces", "storage_policy", new_column_name="storage_metadata")
    op.alter_column("runtime_spaces", "cleanup_policy", new_column_name="cleanup_metadata")


def downgrade() -> None:
    op.alter_column("runtime_spaces", "cleanup_metadata", new_column_name="cleanup_policy")
    op.alter_column("runtime_spaces", "storage_metadata", new_column_name="storage_policy")
