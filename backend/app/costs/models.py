from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ModelPricingRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_pricing_rules"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "model",
            "version",
            name="uq_model_pricing_rules_version",
        ),
        CheckConstraint("input_rate_per_million >= 0", name="ck_pricing_input_rate"),
        CheckConstraint("output_rate_per_million >= 0", name="ck_pricing_output_rate"),
        CheckConstraint(
            "cached_input_rate_per_million IS NULL OR cached_input_rate_per_million >= 0",
            name="ck_pricing_cached_input_rate",
        ),
        CheckConstraint("request_rate >= 0", name="ck_pricing_request_rate"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_pricing_status"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_pricing_effective_window",
        ),
        Index(
            "ix_model_pricing_rules_workspace_lookup",
            "workspace_id",
            "provider",
            "model",
            "status",
            "effective_from",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    input_rate_per_million: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    output_rate_per_million: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    cached_input_rate_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 12),
        nullable=True,
    )
    request_rate: Mapped[Decimal] = mapped_column(
        Numeric(24, 12),
        nullable=False,
        default=Decimal("0"),
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    source: Mapped[str] = mapped_column(String(500), nullable=False, default="operator")


class WorkspaceCostBudget(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_cost_budgets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "currency", name="uq_workspace_cost_budgets_currency"),
        CheckConstraint("monthly_limit > 0", name="ck_cost_budget_monthly_limit"),
        CheckConstraint(
            "warning_ratio > 0 AND warning_ratio <= 1",
            name="ck_cost_budget_warning_ratio",
        ),
        CheckConstraint("enforcement IN ('warn', 'block')", name="ck_cost_budget_enforcement"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    monthly_limit: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    warning_ratio: Mapped[Decimal] = mapped_column(
        Numeric(8, 6),
        nullable=False,
        default=Decimal("0.8"),
    )
    enforcement: Mapped[str] = mapped_column(String(16), nullable=False, default="warn")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ModelUsageRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "model_usage_records"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "agent_run_id",
            "job_attempt",
            name="uq_model_usage_records_run_attempt",
        ),
        CheckConstraint("request_count >= 0", name="ck_model_usage_request_count"),
        CheckConstraint("input_tokens >= 0", name="ck_model_usage_input_tokens"),
        CheckConstraint("output_tokens >= 0", name="ck_model_usage_output_tokens"),
        CheckConstraint("cached_input_tokens >= 0", name="ck_model_usage_cached_tokens"),
        CheckConstraint("reasoning_tokens >= 0", name="ck_model_usage_reasoning_tokens"),
        CheckConstraint("total_tokens >= 0", name="ck_model_usage_total_tokens"),
        CheckConstraint(
            "total_tokens >= input_tokens + output_tokens",
            name="ck_model_usage_total_covers_components",
        ),
        CheckConstraint("job_attempt >= 0", name="ck_model_usage_job_attempt"),
        CheckConstraint(
            "cached_input_tokens <= input_tokens",
            name="ck_model_usage_cached_within_input",
        ),
        CheckConstraint(
            "reasoning_tokens <= output_tokens",
            name="ck_model_usage_reasoning_within_output",
        ),
        CheckConstraint(
            "metering_status IN ('priced', 'unpriced', 'missing_usage')",
            name="ck_model_usage_metering_status",
        ),
        CheckConstraint(
            "metering_status != 'priced' OR ("
            "pricing_rule_id IS NOT NULL AND pricing_version IS NOT NULL "
            "AND currency IS NOT NULL AND input_cost IS NOT NULL "
            "AND output_cost IS NOT NULL AND cached_input_cost IS NOT NULL "
            "AND request_cost IS NOT NULL AND total_cost IS NOT NULL)",
            name="ck_model_usage_priced_fields",
        ),
        CheckConstraint(
            "(input_cost IS NULL OR input_cost >= 0) "
            "AND (output_cost IS NULL OR output_cost >= 0) "
            "AND (cached_input_cost IS NULL OR cached_input_cost >= 0) "
            "AND (request_cost IS NULL OR request_cost >= 0) "
            "AND (total_cost IS NULL OR total_cost >= 0)",
            name="ck_model_usage_nonnegative_costs",
        ),
        CheckConstraint(
            "total_cost IS NULL OR total_cost = "
            "input_cost + output_cost + cached_input_cost + request_cost",
            name="ck_model_usage_cost_components",
        ),
        Index("ix_model_usage_workspace_occurred", "workspace_id", "occurred_at"),
        Index("ix_model_usage_workspace_provider", "workspace_id", "provider", "occurred_at"),
        Index("ix_model_usage_workspace_model", "workspace_id", "model", "occurred_at"),
        Index("ix_model_usage_workspace_agent", "workspace_id", "agent_profile_id", "occurred_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    pricing_rule_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_pricing_rules.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    model_api: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pricing_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    metering_status: Mapped[str] = mapped_column(String(32), nullable=False)
    job_attempt: Mapped[int] = mapped_column(nullable=False, default=0)
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    input_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    output_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    cached_input_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    request_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    raw_usage: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
