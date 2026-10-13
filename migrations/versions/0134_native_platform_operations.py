"""Keep platform evidence and explicitly retire old maintenance execution intents."""

from alembic import op

revision = "0134_native_platform_operations"
down_revision = "0133_approval_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The old dispatcher cannot be resumed into a different durable execution engine.
    op.execute("""UPDATE admin_operation_requests SET status='failed',
        error_code='native_engine_migration_requires_new_request'
        WHERE status IN ('pending','running')""")


def downgrade() -> None:
    # A completed or explicitly retired operator decision must not execute again.
    pass
