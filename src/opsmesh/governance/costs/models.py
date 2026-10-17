"""Governance cost facts: pricing versions, budgets, reservations and usage.

This module is the single owner of cost accounting. It never reads the
identity quota tables or the workspace capacity tables: user tokens and
workspace concurrency are settled by their own owners, and the three
ledgers are composed inside one caller transaction under the documented
lock order. Money is Decimal end to end; a reservation always pins the
pricing rule version and a verifiable cost upper bound, whatever the
budget configuration state is.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

PRICING_SUBJECT_MODEL = "model"
PRICING_SUBJECT_TOOL = "tool"
PRICING_SUBJECTS = (PRICING_SUBJECT_MODEL, PRICING_SUBJECT_TOOL)

PRICING_STATUS_ACTIVE = "active"
PRICING_STATUS_RETIRED = "retired"
PRICING_STATUSES = (PRICING_STATUS_ACTIVE, PRICING_STATUS_RETIRED)

BUDGET_STATE_UNCONFIGURED = "unconfigured"
BUDGET_STATE_UNLIMITED = "unlimited"
BUDGET_STATE_LIMITED = "limited"
BUDGET_STATES = (BUDGET_STATE_UNCONFIGURED, BUDGET_STATE_UNLIMITED, BUDGET_STATE_LIMITED)

BUDGET_PERIOD_MONTHLY = "monthly"
BUDGET_PERIOD_NONE = "none"
BUDGET_PERIODS = (BUDGET_PERIOD_MONTHLY, BUDGET_PERIOD_NONE)

BUDGET_ENFORCEMENT_WARN = "warn"
BUDGET_ENFORCEMENT_BLOCK = "block"
BUDGET_ENFORCEMENTS = (BUDGET_ENFORCEMENT_WARN, BUDGET_ENFORCEMENT_BLOCK)

RESERVATION_RESERVED = "reserved"
RESERVATION_SETTLED = "settled"
RESERVATION_RELEASED = "released"
RESERVATION_UNCERTAIN = "uncertain"
RESERVATION_STATUSES = (
    RESERVATION_RESERVED,
    RESERVATION_SETTLED,
    RESERVATION_RELEASED,
    RESERVATION_UNCERTAIN,
)

RECONCILE_NOT_EXECUTED = "not_executed"
RECONCILE_SUCCEEDED = "succeeded"
RECONCILE_FAILED = "failed"
RECONCILE_OUTCOMES = (RECONCILE_NOT_EXECUTED, RECONCILE_SUCCEEDED, RECONCILE_FAILED)

MAX_CURRENCY_LENGTH = 8
MAX_SUBJECT_KEY_LENGTH = 255
MAX_OPERATION_ID_LENGTH = 255
MAX_RUN_REF_LENGTH = 255
MAX_WINDOW_KEY_LENGTH = 32
# Prices are per single unit (token or tool call); amounts are money.
PRICE_PRECISION = 18
AMOUNT_PRECISION = 12


class CostPricingRuleVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable price sheet for one model or tool subject.

    Versions append per (subject_kind, subject_key): publishing a new
    version never rewrites an admitted reservation, which keeps the pinned
    price of every held operation verifiable after the fact.
    """

    __tablename__ = "cost_pricing_rule_versions"
    __table_args__ = (
        CheckConstraint(
            "subject_kind IN ('model', 'tool')", name="cost_pricing_subject_kind"
        ),
        CheckConstraint(
            "status IN ('active', 'retired')", name="cost_pricing_status"
        ),
        UniqueConstraint(
            "subject_kind",
            "subject_key",
            "version",
            name="uq_cost_pricing_rule_versions_subject_version",
        ),
    )

    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(MAX_SUBJECT_KEY_LENGTH), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(MAX_CURRENCY_LENGTH), nullable=False)
    input_price: Mapped[Decimal] = mapped_column(
        Numeric(24, PRICE_PRECISION), nullable=False, default=Decimal(0)
    )
    output_price: Mapped[Decimal] = mapped_column(
        Numeric(24, PRICE_PRECISION), nullable=False, default=Decimal(0)
    )
    cached_input_price: Mapped[Decimal | None] = mapped_column(Numeric(24, PRICE_PRECISION))
    tool_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, PRICE_PRECISION))
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PRICING_STATUS_ACTIVE
    )
    published_by_user_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))


class CostBudget(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One workspace's cost budget in one currency, with its live window.

    The three configuration states are distinct facts: no row at all is
    ``unconfigured`` (admitted, never a silent unlimited), ``unlimited`` is
    an explicit recorded decision, and a limit carries its period and
    enforcement. ``revision`` advances on every change so concurrent
    writers state the version they saw.
    """

    __tablename__ = "cost_budgets"
    __table_args__ = (
        CheckConstraint("period IN ('monthly', 'none')", name="cost_budget_period"),
        CheckConstraint(
            "enforcement IN ('warn', 'block')", name="cost_budget_enforcement"
        ),
        CheckConstraint(
            "limit_amount IS NULL OR limit_amount > 0", name="cost_budget_limit_amount"
        ),
        UniqueConstraint("workspace_id", "currency", name="uq_cost_budgets_scope_currency"),
    )

    workspace_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(MAX_CURRENCY_LENGTH), nullable=False)
    limit_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, AMOUNT_PRECISION))
    unlimited: Mapped[bool] = mapped_column(nullable=False, default=False)
    period: Mapped[str] = mapped_column(
        String(16), nullable=False, default=BUDGET_PERIOD_MONTHLY
    )
    enforcement: Mapped[str] = mapped_column(
        String(16), nullable=False, default=BUDGET_ENFORCEMENT_BLOCK
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_window_key: Mapped[str] = mapped_column(
        String(MAX_WINDOW_KEY_LENGTH), nullable=False, default=""
    )
    window_used: Mapped[Decimal] = mapped_column(
        Numeric(24, AMOUNT_PRECISION), nullable=False, default=Decimal(0)
    )
    window_reserved: Mapped[Decimal] = mapped_column(
        Numeric(24, AMOUNT_PRECISION), nullable=False, default=Decimal(0)
    )
    unlimited_decided_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True)
    )
    unlimited_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CostBudgetWindow(UUIDPrimaryKeyMixin, Base):
    """One closed period window of one budget, with its totals at close.

    A window stops accepting new reservations when the period rolls but
    stays mutable for settlement: a reservation admitted in window W
    settles into W even after W+1 opened, because the cost was incurred
    in W. The live window is the budget row's own counters, not a row
    here.
    """

    __tablename__ = "cost_budget_windows"
    __table_args__ = (
        UniqueConstraint("budget_id", "window_key", name="uq_cost_budget_windows_budget_key"),
    )

    budget_id: Mapped[UUID] = mapped_column(
        ForeignKey("cost_budgets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    window_key: Mapped[str] = mapped_column(String(MAX_WINDOW_KEY_LENGTH), nullable=False)
    used: Mapped[Decimal] = mapped_column(
        Numeric(24, AMOUNT_PRECISION), nullable=False, default=Decimal(0)
    )
    reserved: Mapped[Decimal] = mapped_column(
        Numeric(24, AMOUNT_PRECISION), nullable=False, default=Decimal(0)
    )
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CostBudgetReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One held cost upper bound for one stable operation id.

    The pinned prices, the pricing rule version and ``max_cost`` are the
    verifiable upper bound; ``amount`` is what the budget headroom holds.
    ``uncertain`` is a persisted reconciliation state with its evidence —
    it never grants a retry permission by itself.
    """

    __tablename__ = "cost_budget_reservations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('reserved', 'settled', 'released', 'uncertain')",
            name="cost_budget_reservation_status",
        ),
        CheckConstraint(
            "budget_state IN ('unconfigured', 'unlimited', 'limited')",
            name="cost_budget_reservation_budget_state",
        ),
        CheckConstraint("max_cost >= 0", name="cost_budget_reservation_max_cost"),
        UniqueConstraint("operation_id", name="uq_cost_budget_reservations_operation"),
        Index("ix_cost_budget_reservations_status", "status"),
    )

    operation_id: Mapped[str] = mapped_column(
        String(MAX_OPERATION_ID_LENGTH), nullable=False
    )
    workspace_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True), index=True)
    currency: Mapped[str] = mapped_column(String(MAX_CURRENCY_LENGTH), nullable=False)
    budget_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cost_budgets.id", ondelete="RESTRICT"), index=True
    )
    budget_state: Mapped[str] = mapped_column(String(16), nullable=False)
    window_key: Mapped[str | None] = mapped_column(String(MAX_WINDOW_KEY_LENGTH))
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(MAX_SUBJECT_KEY_LENGTH), nullable=False)
    pricing_rule_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("cost_pricing_rule_versions.id", ondelete="RESTRICT"), nullable=False
    )
    unit_prices: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    max_cost: Mapped[Decimal] = mapped_column(Numeric(24, AMOUNT_PRECISION), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, AMOUNT_PRECISION), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RESERVATION_RESERVED
    )
    run_ref: Mapped[str | None] = mapped_column(String(MAX_RUN_REF_LENGTH))
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    uncertain_reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    settled_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, AMOUNT_PRECISION))
    overage_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, AMOUNT_PRECISION))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_reason: Mapped[str | None] = mapped_column(Text)


class CostUsageRecord(UUIDPrimaryKeyMixin, Base):
    """One settled operation's actual usage and cost, deduplicated by id.

    The actual cost is recorded in full even when it exceeds the held
    upper bound; the excess is its own fact, never a silent truncation.
    """

    __tablename__ = "cost_usage_records"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_cost_usage_records_operation"),
    )

    operation_id: Mapped[str] = mapped_column(
        String(MAX_OPERATION_ID_LENGTH), nullable=False
    )
    reservation_id: Mapped[UUID] = mapped_column(
        ForeignKey("cost_budget_reservations.id", ondelete="RESTRICT"), nullable=False
    )
    workspace_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True), index=True)
    window_key: Mapped[str | None] = mapped_column(String(MAX_WINDOW_KEY_LENGTH), index=True)
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(MAX_SUBJECT_KEY_LENGTH), nullable=False)
    pricing_rule_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("cost_pricing_rule_versions.id", ondelete="RESTRICT"), nullable=False
    )
    usage: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    actual_cost: Mapped[Decimal] = mapped_column(Numeric(24, AMOUNT_PRECISION), nullable=False)
    overage_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, AMOUNT_PRECISION), nullable=False, default=Decimal(0)
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
