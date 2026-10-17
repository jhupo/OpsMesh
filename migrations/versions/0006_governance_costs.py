"""governance costs: pricing versions, budgets, windows, reservations and usage

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-18 00:00:00.000000+00:00

S6.5 tables: the cost ledger governance owns. Prices, budgets, budget
windows, reservations and usage records each have exactly one owner here;
the identity quota and workspace capacity tables are never read by this
module. Money is Numeric end to end and every reservation pins its
pricing rule version and cost upper bound. No historical architecture
migration is inherited.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cost_pricing_rule_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("subject_key", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column(
            "input_price", sa.Numeric(precision=24, scale=18), nullable=False
        ),
        sa.Column(
            "output_price", sa.Numeric(precision=24, scale=18), nullable=False
        ),
        sa.Column("cached_input_price", sa.Numeric(precision=24, scale=18), nullable=True),
        sa.Column("tool_unit_price", sa.Numeric(precision=24, scale=18), nullable=True),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("published_by_user_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_pricing_rule_versions")),
        sa.CheckConstraint(
            "subject_kind IN ('model', 'tool')", name="cost_pricing_subject_kind"
        ),
        sa.CheckConstraint("status IN ('active', 'retired')", name="cost_pricing_status"),
        sa.UniqueConstraint(
            "subject_kind",
            "subject_key",
            "version",
            name="uq_cost_pricing_rule_versions_subject_version",
        ),
    )
    op.create_table(
        "cost_budgets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("limit_amount", sa.Numeric(precision=24, scale=12), nullable=True),
        sa.Column("unlimited", sa.Boolean(), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("enforcement", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("current_window_key", sa.String(length=32), nullable=False),
        sa.Column(
            "window_used", sa.Numeric(precision=24, scale=12), nullable=False
        ),
        sa.Column(
            "window_reserved", sa.Numeric(precision=24, scale=12), nullable=False
        ),
        sa.Column("unlimited_decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("unlimited_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_budgets")),
        sa.CheckConstraint("period IN ('monthly', 'none')", name="cost_budget_period"),
        sa.CheckConstraint(
            "enforcement IN ('warn', 'block')", name="cost_budget_enforcement"
        ),
        sa.CheckConstraint(
            "limit_amount IS NULL OR limit_amount > 0", name="cost_budget_limit_amount"
        ),
        sa.UniqueConstraint(
            "workspace_id", "currency", name="uq_cost_budgets_scope_currency"
        ),
    )
    op.create_table(
        "cost_budget_windows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("budget_id", sa.Uuid(), nullable=False),
        sa.Column("window_key", sa.String(length=32), nullable=False),
        sa.Column("used", sa.Numeric(precision=24, scale=12), nullable=False),
        sa.Column("reserved", sa.Numeric(precision=24, scale=12), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_budget_windows")),
        sa.ForeignKeyConstraint(
            ["budget_id"],
            ["cost_budgets.id"],
            ondelete="RESTRICT",
            name=op.f("fk_cost_budget_windows_budget_id_cost_budgets"),
        ),
        sa.UniqueConstraint(
            "budget_id", "window_key", name="uq_cost_budget_windows_budget_key"
        ),
    )
    op.create_index(
        op.f("ix_cost_budget_windows_budget_id"),
        "cost_budget_windows",
        ["budget_id"],
        unique=False,
    )
    op.create_table(
        "cost_budget_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("operation_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("budget_id", sa.Uuid(), nullable=True),
        sa.Column("budget_state", sa.String(length=16), nullable=False),
        sa.Column("window_key", sa.String(length=32), nullable=True),
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("subject_key", sa.String(length=255), nullable=False),
        sa.Column("pricing_rule_version_id", sa.Uuid(), nullable=False),
        sa.Column(
            "unit_prices",
            postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column("max_cost", sa.Numeric(precision=24, scale=12), nullable=False),
        sa.Column("amount", sa.Numeric(precision=24, scale=12), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("run_ref", sa.String(length=255), nullable=True),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("uncertain_reason", sa.Text(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("settled_amount", sa.Numeric(precision=24, scale=12), nullable=True),
        sa.Column("overage_amount", sa.Numeric(precision=24, scale=12), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_budget_reservations")),
        sa.CheckConstraint(
            "status IN ('reserved', 'settled', 'released', 'uncertain')",
            name="cost_budget_reservation_status",
        ),
        sa.CheckConstraint(
            "budget_state IN ('unconfigured', 'unlimited', 'limited')",
            name="cost_budget_reservation_budget_state",
        ),
        sa.CheckConstraint("max_cost >= 0", name="cost_budget_reservation_max_cost"),
        sa.UniqueConstraint(
            "operation_id", name="uq_cost_budget_reservations_operation"
        ),
        sa.ForeignKeyConstraint(
            ["budget_id"],
            ["cost_budgets.id"],
            ondelete="RESTRICT",
            name=op.f("fk_cost_budget_reservations_budget_id_cost_budgets"),
        ),
        sa.ForeignKeyConstraint(
            ["pricing_rule_version_id"],
            ["cost_pricing_rule_versions.id"],
            ondelete="RESTRICT",
            name=op.f("fk_cost_budget_reservations_pricing_rule_version_id"),
        ),
    )
    op.create_index(
        op.f("ix_cost_budget_reservations_budget_id"),
        "cost_budget_reservations",
        ["budget_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cost_budget_reservations_status"),
        "cost_budget_reservations",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cost_budget_reservations_workspace_id"),
        "cost_budget_reservations",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "cost_usage_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.String(length=255), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("window_key", sa.String(length=32), nullable=True),
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("subject_key", sa.String(length=255), nullable=False),
        sa.Column("pricing_rule_version_id", sa.Uuid(), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("actual_cost", sa.Numeric(precision=24, scale=12), nullable=False),
        sa.Column(
            "overage_amount", sa.Numeric(precision=24, scale=12), nullable=False
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_usage_records")),
        sa.UniqueConstraint("operation_id", name="uq_cost_usage_records_operation"),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["cost_budget_reservations.id"],
            ondelete="RESTRICT",
            name=op.f("fk_cost_usage_records_reservation_id_cost_budget_reservations"),
        ),
        sa.ForeignKeyConstraint(
            ["pricing_rule_version_id"],
            ["cost_pricing_rule_versions.id"],
            ondelete="RESTRICT",
            name=op.f("fk_cost_usage_records_pricing_rule_version_id"),
        ),
    )
    op.create_index(
        op.f("ix_cost_usage_records_workspace_id"),
        "cost_usage_records",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cost_usage_records_window_key"),
        "cost_usage_records",
        ["window_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_cost_usage_records_window_key"), table_name="cost_usage_records"
    )
    op.drop_index(
        op.f("ix_cost_usage_records_workspace_id"), table_name="cost_usage_records"
    )
    op.drop_table("cost_usage_records")
    op.drop_index(
        op.f("ix_cost_budget_reservations_budget_id"),
        table_name="cost_budget_reservations",
    )
    op.drop_index(
        op.f("ix_cost_budget_reservations_workspace_id"),
        table_name="cost_budget_reservations",
    )
    op.drop_index(
        op.f("ix_cost_budget_reservations_status"),
        table_name="cost_budget_reservations",
    )
    op.drop_table("cost_budget_reservations")
    op.drop_index(
        op.f("ix_cost_budget_windows_budget_id"), table_name="cost_budget_windows"
    )
    op.drop_table("cost_budget_windows")
    op.drop_table("cost_budgets")
    op.drop_table("cost_pricing_rule_versions")
