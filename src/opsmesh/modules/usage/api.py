"""Request usage, versioned prices and reservation contracts."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import Field, model_validator

from opsmesh.framework.typing import OpsMeshContract


class PriceVersion(OpsMeshContract):
    id: str
    model_profile: str
    currency: str = Field(min_length=3, max_length=3)
    input_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    cached_input_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    effective_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UsageFact(OpsMeshContract):
    call_id: str
    principal_id: str
    run_id: str
    model_profile: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    provider_usage: dict[str, int | float] = Field(default_factory=dict)
    kind: Literal["request", "cumulative"] = "request"
    sequence: int = Field(default=0, ge=0)
    parent_run_id: str | None = None
    reservation_id: str | None = None
    usage_available: bool = True
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_identity_and_counts(self) -> "UsageFact":
        if not all((self.call_id, self.principal_id, self.run_id, self.model_profile)):
            raise ValueError("Usage identities must be nonempty")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("Cached input is a subset of total input")
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("Reasoning tokens are a subset of output tokens")
        if self.parent_run_id == self.run_id:
            raise ValueError("A run cannot be its own parent")
        return self


class CostRecord(OpsMeshContract):
    call_id: str
    principal_id: str
    run_id: str
    model_profile: str
    status: Literal["priced", "unpriced", "unavailable", "ignored"]
    amount: Decimal | None = None
    currency: str | None = None
    price_version_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    resource_key: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    source_amount: Decimal | None = None
    source_currency: str | None = None
    exchange_rate_id: str | None = None
    reservation_issue: str | None = None


class ResourcePriceVersion(OpsMeshContract):
    """Configured tariffs for hosted calls, compute, storage and other measured resources."""

    id: str = Field(min_length=1)
    resource_key: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount_per_unit: Decimal = Field(ge=0, allow_inf_nan=False)
    effective_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ResourceUsageFact(OpsMeshContract):
    call_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    resource_key: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    quantity: Decimal = Field(ge=0, allow_inf_nan=False)
    usage_available: bool = True
    reservation_id: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExchangeRateVersion(OpsMeshContract):
    id: str = Field(min_length=1)
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")
    quote_currency: str = Field(pattern=r"^[A-Z]{3}$")
    rate: Decimal = Field(gt=0, allow_inf_nan=False)
    effective_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProviderInvoiceLine(OpsMeshContract):
    provider: str = Field(min_length=1)
    invoice_id: str = Field(min_length=1)
    line_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    amount: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class InvoiceReconciliation(OpsMeshContract):
    line: ProviderInvoiceLine
    status: Literal["matched", "difference", "missing_usage", "unpriced", "currency_mismatch"]
    recorded_amount: Decimal | None = None
    difference: Decimal | None = None


class BudgetLimit(OpsMeshContract):
    principal_id: str
    currency: str = Field(min_length=3, max_length=3)
    amount: Decimal = Field(ge=0, allow_inf_nan=False)


class BudgetReservation(OpsMeshContract):
    id: str
    principal_id: str
    currency: str
    amount: Decimal
    remaining: Decimal
    status: Literal["reserved", "pending_pricing", "settled"] = "reserved"
    actual: Decimal | None = None


class BudgetStatus(OpsMeshContract):
    principal_id: str
    currency: str
    limit: Decimal | None
    spent: Decimal
    reserved: Decimal
    available: Decimal | None
    unpriced_observations: int


class UsageService(Protocol):
    async def list_prices(self) -> tuple[PriceVersion, ...]: ...
    async def list_resource_prices(self) -> tuple[ResourcePriceVersion, ...]: ...
    async def list_exchange_rates(self) -> tuple[ExchangeRateVersion, ...]: ...
    async def list_reconciliations(
        self, principal_id: str
    ) -> tuple[InvoiceReconciliation, ...]: ...
    async def publish_price(self, price: PriceVersion) -> PriceVersion: ...
    async def publish_resource_price(self, price: ResourcePriceVersion) -> ResourcePriceVersion: ...
    async def publish_exchange_rate(self, rate: ExchangeRateVersion) -> ExchangeRateVersion: ...
    async def record_resource(self, fact: ResourceUsageFact) -> CostRecord: ...
    async def reprice(self, principal_id: str) -> tuple[CostRecord, ...]: ...
    async def reconcile(self, line: ProviderInvoiceLine) -> InvoiceReconciliation: ...
    async def set_budget(self, limit: BudgetLimit) -> BudgetStatus: ...
    async def record(self, fact: UsageFact) -> CostRecord: ...
    async def has_request_usage(self, principal_id: str, run_id: str) -> bool: ...
    async def reserve(
        self, principal_id: str, reservation_id: str, amount: Decimal, currency: str
    ) -> BudgetReservation: ...
    async def settle(
        self, principal_id: str, reservation_id: str, actual: Decimal | None
    ) -> BudgetReservation: ...
    async def costs(
        self, principal_id: str, run_id: str | None = None
    ) -> tuple[CostRecord, ...]: ...
    async def budget(self, principal_id: str, currency: str) -> BudgetStatus: ...
