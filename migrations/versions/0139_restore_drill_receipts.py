"""Durable temporary-object receipts and explicit restore-drill reconciliation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0139_restore_drill_receipts"
down_revision = "0138_execution_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_workspace_export_jobs_scope", "workspace_export_jobs", ["workspace_id", "id"]
    )
    op.create_table(
        "workspace_restore_drill_operations",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(), nullable=False),
        sa.Column("export_job_id", postgresql.UUID(), nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=True),
        sa.Column("temporary_workspace_id", postgresql.UUID(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("storage_keys", postgresql.JSONB(), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "export_job_id"],
            ["workspace_export_jobs.workspace_id", "workspace_export_jobs.id"],
            name="fk_restore_drill_export_scope",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'unknown', 'reconciled')",
            name=op.f("ck_workspace_restore_drill_operations_restore_drill_status"),
        ),
    )
    op.create_index(
        "ix_restore_drill_workspace_status",
        "workspace_restore_drill_operations",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM workspace_restore_drill_operations WHERE status IN ('running','unknown')"
        )
    ):
        raise RuntimeError("Reconcile unfinished restore drills before downgrading")
    op.drop_table("workspace_restore_drill_operations")
    op.drop_constraint("uq_workspace_export_jobs_scope", "workspace_export_jobs", type_="unique")
