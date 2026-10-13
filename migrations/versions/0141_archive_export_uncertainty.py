"""Make interrupted archive writers explicit without replaying unknown writes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0141_archive_export_uncertainty"
down_revision = "0140_native_plugin_downloads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_export_jobs",
        sa.Column(
            "execution_identity",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("workspace_export_jobs", "execution_identity", server_default=None)
    op.execute(
        "UPDATE workspace_export_jobs SET execution_identity = jsonb_build_object("
        "'user_id', created_by_user_id::text, 'token_id', NULL, 'token_scopes', NULL) "
        "WHERE created_by_user_id IS NOT NULL"
    )
    op.execute(
        "UPDATE workspace_export_jobs SET status = 'unknown', "
        "error = 'archive_export_outcome_unknown' WHERE status IN ('queued', 'running')"
    )
    op.create_check_constraint(
        op.f("ck_workspace_export_jobs_export_job_status"),
        "workspace_export_jobs",
        "status IN ('queued', 'running', 'completed', 'failed', 'unknown', 'reconciled')",
    )
    op.drop_constraint(
        op.f("ck_workspace_restore_drill_operations_restore_drill_status"),
        "workspace_restore_drill_operations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_workspace_restore_drill_operations_restore_drill_status"),
        "workspace_restore_drill_operations",
        "status IN ('queued', 'running', 'completed', 'failed', 'unknown', 'reconciled')",
    )
    for name in ("request", "execution_identity"):
        op.add_column(
            "workspace_restore_drill_operations",
            sa.Column(
                name, postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
            ),
        )
        op.alter_column("workspace_restore_drill_operations", name, server_default=None)
    op.add_column(
        "workspace_restore_drill_operations", sa.Column("result", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "workspace_restore_drill_operations",
        sa.Column("origin", sa.String(40), nullable=False, server_default="user"),
    )
    op.alter_column("workspace_restore_drill_operations", "origin", server_default=None)
    op.execute(
        "UPDATE workspace_restore_drill_operations SET status='unknown', error_code='restore_drill_outcome_unknown' WHERE status='running'"
    )
    op.create_table(
        "workspace_archive_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("export_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("execution_identity", postgresql.JSONB(), nullable=False),
        sa.Column("origin", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "export_job_id"],
            ["workspace_export_jobs.workspace_id", "workspace_export_jobs.id"],
            name="fk_archive_verification_export_scope",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name=op.f("ck_workspace_archive_verifications_archive_verification_status"),
        ),
    )
    op.create_index(
        "ix_archive_verifications_workspace_status",
        "workspace_archive_verifications",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM workspace_export_jobs WHERE status IN ('queued','running','unknown'))"
        )
    ):
        raise RuntimeError("Settle pending or uncertain archive exports before downgrading")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM workspace_restore_drill_operations WHERE status IN ('queued','running','unknown'))"
        )
    ):
        raise RuntimeError("Settle pending restore drills before downgrading")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM workspace_archive_verifications WHERE status IN ('queued','running'))"
        )
    ):
        raise RuntimeError("Settle pending archive verifications before downgrading")
    op.drop_table("workspace_archive_verifications")
    for name in ("request", "result", "execution_identity", "origin"):
        op.drop_column("workspace_restore_drill_operations", name)
    op.drop_constraint(
        op.f("ck_workspace_restore_drill_operations_restore_drill_status"),
        "workspace_restore_drill_operations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_workspace_restore_drill_operations_restore_drill_status"),
        "workspace_restore_drill_operations",
        "status IN ('running','completed','failed','unknown','reconciled')",
    )
    op.drop_constraint(
        op.f("ck_workspace_export_jobs_export_job_status"), "workspace_export_jobs", type_="check"
    )
    op.execute("UPDATE workspace_export_jobs SET status = 'failed' WHERE status = 'reconciled'")
    op.drop_column("workspace_export_jobs", "execution_identity")
