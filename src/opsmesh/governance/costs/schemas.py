"""Governance cost HTTP contracts and the standard usage value type.

``NormalizedUsage`` is the standard usage shape every priced operation
reports — the SDK maps its ``Usage`` value onto it and execution
normalizes model snapshots into it. No contract here carries a secret:
prices and budgets are configuration facts, credentials never are.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opsmesh.governance.costs.models import (
    MAX_CURRENCY_LENGTH,
    MAX_SUBJECT_KEY_LENGTH,
)


class NormalizedUsage(BaseModel):
    """The standard usage snapshot of one priced operation.

    One shape serves models (token counts) and tools (invocation count);
    a model operation leaves the tool fields zero and vice versa.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    tool_invocations: int = Field(default=0, ge=0)


class UnitPrices(BaseModel):
    """The pinned price sheet of one subject, per single unit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str = Field(min_length=1, max_length=MAX_CURRENCY_LENGTH)
    input_price: Decimal = Field(default=Decimal(0), ge=0)
    output_price: Decimal = Field(default=Decimal(0), ge=0)
    cached_input_price: Decimal | None = Field(default=None, ge=0)
    tool_unit_price: Decimal | None = Field(default=None, ge=0)


class PricingVersionPublishRequest(BaseModel):
    """Publish one immutable price sheet for one model or tool subject."""

    model_config = ConfigDict(extra="forbid")

    subject_kind: str = Field(max_length=16)
    subject_key: str = Field(min_length=1, max_length=MAX_SUBJECT_KEY_LENGTH)
    prices: UnitPrices


class PricingVersionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pricing_version_id: UUID
    subject_kind: str
    subject_key: str
    version: int
    prices: UnitPrices
    digest: str
    status: str
    published_by_user_id: UUID | None
    created_at: datetime


class PricingVersionPage(BaseModel):
    versions: list[PricingVersionView]
    total: int


class BudgetUpsertRequest(BaseModel):
    """Create or change one workspace budget; a change states its revision."""

    model_config = ConfigDict(extra="forbid")

    currency: str = Field(min_length=1, max_length=MAX_CURRENCY_LENGTH)
    limit_amount: Decimal | None = Field(default=None, gt=0)
    unlimited: bool = False
    period: str = Field(default="monthly", max_length=16)
    enforcement: str = Field(default="block", max_length=16)
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _exactly_one_of_limit_or_unlimited(self) -> BudgetUpsertRequest:
        if (self.limit_amount is None) == (not self.unlimited):
            msg = "A budget declares either limit_amount or unlimited, not both and not neither"
            raise ValueError(msg)
        return self


class BudgetView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    budget_id: UUID
    workspace_id: UUID
    currency: str
    budget_state: str
    limit_amount: Decimal | None
    unlimited: bool
    period: str
    enforcement: str
    revision: int
    current_window_key: str
    window_used: Decimal
    window_reserved: Decimal
    unlimited_decided_by_user_id: UUID | None
    unlimited_decided_at: datetime | None
    updated_at: datetime


class UsageView(BaseModel):
    operation_id: str
    subject_kind: str
    subject_key: str
    usage: NormalizedUsage
    actual_cost: Decimal
    overage_amount: Decimal
    pricing_version_id: UUID
    recorded_at: datetime


class UsagePage(BaseModel):
    usages: list[UsageView]
    total: int


class ReservationView(BaseModel):
    """One held or closed cost reservation; the pending set reads this."""

    model_config = ConfigDict(from_attributes=True)

    operation_id: str
    workspace_id: UUID | None
    currency: str
    budget_state: str
    window_key: str | None
    subject_kind: str
    subject_key: str
    pricing_version_id: UUID
    prices: UnitPrices
    max_cost: Decimal
    amount: Decimal
    status: str
    run_ref: str | None
    uncertain_reason: str | None
    settled_amount: Decimal | None
    overage_amount: Decimal | None
    created_at: datetime
    settled_at: datetime | None
    released_at: datetime | None
    released_reason: str | None


class ReservationPage(BaseModel):
    reservations: list[ReservationView]
    total: int


class ReconcileRequest(BaseModel):
    """Confirm one uncertain operation with its evidence and expected revision."""

    model_config = ConfigDict(extra="forbid")

    outcome: str = Field(max_length=32)
    actual_usage: NormalizedUsage | None = None
    evidence: dict[str, object] = Field(default_factory=dict)


__all__ = [
    "BudgetUpsertRequest",
    "BudgetView",
    "NormalizedUsage",
    "PricingVersionPage",
    "PricingVersionPublishRequest",
    "PricingVersionView",
    "ReconcileRequest",
    "ReservationPage",
    "ReservationView",
    "UnitPrices",
    "UsagePage",
    "UsageView",
]
