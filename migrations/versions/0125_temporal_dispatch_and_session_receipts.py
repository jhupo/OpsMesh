"""Replace queue delivery with Temporal dispatch."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0125_temporal_dispatch_session_receipts"
down_revision = "0124_native_sandbox_references"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM queue_dispatches WHERE published_at IS NULL")
    ):
        raise RuntimeError("Drain old delivery intents before replacing the queue outbox")
    op.drop_table("queue_dispatches")
    op.create_table(
        "temporal_dispatches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_id", sa.String(255), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "operation IN ('start', 'signal', 'cancel')", name="temporal_dispatch_operation"
        ),
    )
    op.create_index(
        "ix_temporal_dispatches_pending", "temporal_dispatches", ["published_at", "available_at"]
    )
    op.create_index("ix_temporal_dispatches_workspace_id", "temporal_dispatches", ["workspace_id"])
    op.create_index("ix_temporal_dispatches_workflow_id", "temporal_dispatches", ["workflow_id"])
    op.create_index("ix_temporal_dispatches_claim_token", "temporal_dispatches", ["claim_token"])


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM temporal_dispatches WHERE published_at IS NULL")
    ):
        raise RuntimeError("Drain Temporal delivery intents before downgrading")
    op.drop_index("ix_temporal_dispatches_workflow_id", table_name="temporal_dispatches")
    op.drop_index("ix_temporal_dispatches_workspace_id", table_name="temporal_dispatches")
    op.drop_index("ix_temporal_dispatches_pending", table_name="temporal_dispatches")
    op.drop_index("ix_temporal_dispatches_claim_token", table_name="temporal_dispatches")
    op.drop_table("temporal_dispatches")
    op.create_table(
        "queue_dispatches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_queue_dispatches_pending", "queue_dispatches", ["published_at", "available_at"]
    )
    op.create_index("ix_queue_dispatches_workspace_id", "queue_dispatches", ["workspace_id"])
