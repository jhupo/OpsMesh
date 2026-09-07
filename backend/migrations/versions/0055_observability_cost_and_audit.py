"""complete observability cost and audit foundations

Revision ID: 0055_observability_cost_audit
Revises: 0054_unified_marketplace
Create Date: 2026-09-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0055_observability_cost_audit"
down_revision: str | None = "0054_unified_marketplace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_integrity_checks",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("checked_events", sa.Integer(), nullable=False),
        sa.Column("valid", sa.Boolean(), nullable=False),
        sa.Column("broken_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_integrity_checks")),
    )
    op.create_index(
        "ix_audit_integrity_checks_workspace_created",
        "audit_integrity_checks",
        ["workspace_id", "created_at"],
    )

    op.create_table(
        "model_pricing_rules",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("input_rate_per_million", sa.Numeric(24, 12), nullable=False),
        sa.Column("output_rate_per_million", sa.Numeric(24, 12), nullable=False),
        sa.Column("cached_input_rate_per_million", sa.Numeric(24, 12), nullable=True),
        sa.Column("request_rate", sa.Numeric(24, 12), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=500), nullable=False),
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
        sa.CheckConstraint("input_rate_per_million >= 0", name="ck_pricing_input_rate"),
        sa.CheckConstraint("output_rate_per_million >= 0", name="ck_pricing_output_rate"),
        sa.CheckConstraint(
            "cached_input_rate_per_million IS NULL OR cached_input_rate_per_million >= 0",
            name="ck_pricing_cached_input_rate",
        ),
        sa.CheckConstraint("request_rate >= 0", name="ck_pricing_request_rate"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_pricing_status"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_pricing_effective_window",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_pricing_rules")),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "model",
            "version",
            name="uq_model_pricing_rules_version",
        ),
    )
    op.create_index(
        "ix_model_pricing_rules_workspace_lookup",
        "model_pricing_rules",
        ["workspace_id", "provider", "model", "status", "effective_from"],
    )

    op.create_table(
        "workspace_cost_budgets",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("monthly_limit", sa.Numeric(24, 12), nullable=False),
        sa.Column("warning_ratio", sa.Numeric(8, 6), nullable=False),
        sa.Column("enforcement", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint("monthly_limit > 0", name="ck_cost_budget_monthly_limit"),
        sa.CheckConstraint(
            "warning_ratio > 0 AND warning_ratio <= 1",
            name="ck_cost_budget_warning_ratio",
        ),
        sa.CheckConstraint(
            "enforcement IN ('warn', 'block')",
            name="ck_cost_budget_enforcement",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_cost_budgets")),
        sa.UniqueConstraint(
            "workspace_id",
            "currency",
            name="uq_workspace_cost_budgets_currency",
        ),
    )

    op.create_table(
        "model_usage_records",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pricing_rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("model_api", sa.String(length=80), nullable=True),
        sa.Column("pricing_version", sa.String(length=80), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("metering_status", sa.String(length=32), nullable=False),
        sa.Column("job_attempt", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False),
        sa.Column("input_cost", sa.Numeric(24, 12), nullable=True),
        sa.Column("output_cost", sa.Numeric(24, 12), nullable=True),
        sa.Column("cached_input_cost", sa.Numeric(24, 12), nullable=True),
        sa.Column("request_cost", sa.Numeric(24, 12), nullable=True),
        sa.Column("total_cost", sa.Numeric(24, 12), nullable=True),
        sa.Column("raw_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint("request_count >= 0", name="ck_model_usage_request_count"),
        sa.CheckConstraint("input_tokens >= 0", name="ck_model_usage_input_tokens"),
        sa.CheckConstraint("output_tokens >= 0", name="ck_model_usage_output_tokens"),
        sa.CheckConstraint("cached_input_tokens >= 0", name="ck_model_usage_cached_tokens"),
        sa.CheckConstraint("reasoning_tokens >= 0", name="ck_model_usage_reasoning_tokens"),
        sa.CheckConstraint("total_tokens >= 0", name="ck_model_usage_total_tokens"),
        sa.CheckConstraint(
            "total_tokens >= input_tokens + output_tokens",
            name="ck_model_usage_total_covers_components",
        ),
        sa.CheckConstraint("job_attempt >= 0", name="ck_model_usage_job_attempt"),
        sa.CheckConstraint(
            "cached_input_tokens <= input_tokens",
            name="ck_model_usage_cached_within_input",
        ),
        sa.CheckConstraint(
            "reasoning_tokens <= output_tokens",
            name="ck_model_usage_reasoning_within_output",
        ),
        sa.CheckConstraint(
            "metering_status IN ('priced', 'unpriced', 'missing_usage')",
            name="ck_model_usage_metering_status",
        ),
        sa.CheckConstraint(
            "metering_status != 'priced' OR ("
            "pricing_rule_id IS NOT NULL AND pricing_version IS NOT NULL "
            "AND currency IS NOT NULL AND input_cost IS NOT NULL "
            "AND output_cost IS NOT NULL AND cached_input_cost IS NOT NULL "
            "AND request_cost IS NOT NULL AND total_cost IS NOT NULL)",
            name="ck_model_usage_priced_fields",
        ),
        sa.CheckConstraint(
            "(input_cost IS NULL OR input_cost >= 0) "
            "AND (output_cost IS NULL OR output_cost >= 0) "
            "AND (cached_input_cost IS NULL OR cached_input_cost >= 0) "
            "AND (request_cost IS NULL OR request_cost >= 0) "
            "AND (total_cost IS NULL OR total_cost >= 0)",
            name="ck_model_usage_nonnegative_costs",
        ),
        sa.CheckConstraint(
            "total_cost IS NULL OR total_cost = "
            "input_cost + output_cost + cached_input_cost + request_cost",
            name="ck_model_usage_cost_components",
        ),
        sa.ForeignKeyConstraint(
            ["agent_profile_id"],
            ["agent_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pricing_rule_id"],
            ["model_pricing_rules.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_usage_records")),
        sa.UniqueConstraint(
            "workspace_id",
            "agent_run_id",
            "job_attempt",
            name="uq_model_usage_records_run_attempt",
        ),
    )
    op.create_index(
        "ix_model_usage_workspace_occurred",
        "model_usage_records",
        ["workspace_id", "occurred_at"],
    )
    op.create_index(
        "ix_model_usage_workspace_provider",
        "model_usage_records",
        ["workspace_id", "provider", "occurred_at"],
    )
    op.create_index(
        "ix_model_usage_workspace_model",
        "model_usage_records",
        ["workspace_id", "model", "occurred_at"],
    )
    op.create_index(
        "ix_model_usage_workspace_agent",
        "model_usage_records",
        ["workspace_id", "agent_profile_id", "occurred_at"],
    )

    op.execute(
        """
        CREATE FUNCTION opsmesh_protect_audit_events() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND current_setting('opsmesh.audit_retention_delete', true) = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'audit_events are append-only and WORM protected';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_opsmesh_protect_audit_events
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION opsmesh_protect_audit_events();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_opsmesh_protect_audit_events ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS opsmesh_protect_audit_events()")
    op.drop_index("ix_model_usage_workspace_agent", table_name="model_usage_records")
    op.drop_index("ix_model_usage_workspace_model", table_name="model_usage_records")
    op.drop_index("ix_model_usage_workspace_provider", table_name="model_usage_records")
    op.drop_index("ix_model_usage_workspace_occurred", table_name="model_usage_records")
    op.drop_table("model_usage_records")
    op.drop_table("workspace_cost_budgets")
    op.drop_index(
        "ix_model_pricing_rules_workspace_lookup",
        table_name="model_pricing_rules",
    )
    op.drop_table("model_pricing_rules")
    op.drop_index(
        "ix_audit_integrity_checks_workspace_created",
        table_name="audit_integrity_checks",
    )
    op.drop_table("audit_integrity_checks")
