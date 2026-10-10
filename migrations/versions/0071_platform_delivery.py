"""Durable platform update plans and maintenance ownership."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0071_platform_delivery"
down_revision = "0070_memory_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_installation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("maintenance", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("release_manifest", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("id = 1", name="ck_platform_installation_singleton"),
    )
    op.execute("INSERT INTO platform_installation (id) VALUES (1)")
    op.create_table(
        "platform_update_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("active_slot", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("tag", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(40), nullable=False),
        sa.Column("plan", postgresql.JSONB(), nullable=False),
        sa.Column("plan_sha256", sa.String(64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("backup_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_platform_update_jobs_idempotency"),
        sa.UniqueConstraint("active_slot", name="uq_platform_update_jobs_active_slot"),
        sa.CheckConstraint(
            "active_slot IS NULL OR active_slot = 1",
            name="ck_platform_update_jobs_active_slot_valid",
        ),
        sa.CheckConstraint(
            "action IN ('update', 'rollback', 'backup')",
            name="ck_platform_update_jobs_action_valid",
        ),
        sa.CheckConstraint(
            "status IN ('planning', 'ready', 'queued', 'running', 'succeeded', 'failed', 'recovery_required', 'cancelled')",
            name="ck_platform_update_jobs_status_valid",
        ),
    )
    op.create_table(
        "platform_update_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_update_jobs.id"),
            nullable=False,
        ),
        sa.Column("phase", sa.String(40), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("""
        CREATE FUNCTION opsmesh_protect_update_events() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Platform update events are append-only'; END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_opsmesh_update_events_worm BEFORE UPDATE OR DELETE
        ON platform_update_events FOR EACH ROW EXECUTE FUNCTION opsmesh_protect_update_events();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_opsmesh_update_events_worm ON platform_update_events")
    op.execute("DROP FUNCTION opsmesh_protect_update_events()")
    op.drop_table("platform_update_events")
    op.drop_table("platform_update_jobs")
    op.drop_table("platform_installation")
