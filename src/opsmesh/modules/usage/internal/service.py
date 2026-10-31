"""Public usage facade over immutable prices and a per-principal CAS ledger."""

from decimal import Decimal
from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.usage.api import (
    BudgetLimit,
    BudgetReservation,
    BudgetStatus,
    CostRecord,
    ExchangeRateVersion,
    InvoiceReconciliation,
    PriceVersion,
    ProviderInvoiceLine,
    ResourcePriceVersion,
    ResourceUsageFact,
    UsageFact,
)

from .budgets import apply_cost, budget_status, reserve_budget, settle_budget
from .ledger import PrincipalUsageLedger
from .observations import prior_observation, usage_delta
from .pricing import VersionedPrices, price_fact
from .reconciliation import reconcile_line
from .resource_pricing import ResourcePricingCatalog


class PersistentUsageService:
    """All monetary writes for a principal commit in one optimistic transaction.

    `record` books actual priced spend. `settle` only releases an estimate; its `actual`
    is an audit value and is never booked a second time. Unknown actual cost retains a hold.
    """

    def __init__(self, database: AsyncDatabase, *, billing_currency: str | None = None) -> None:
        self._ledger = PrincipalUsageLedger(database)
        self._prices = VersionedPrices(database)
        if billing_currency is not None:
            self._validate_currency(billing_currency)
        self._resources = ResourcePricingCatalog(database, billing_currency)

    async def publish_price(self, price: PriceVersion) -> PriceVersion:
        self._validate_currency(price.currency)
        return await self._prices.publish(price)

    async def list_prices(self) -> tuple[PriceVersion, ...]:
        return await self._prices.list()

    async def list_resource_prices(self) -> tuple[ResourcePriceVersion, ...]:
        return tuple(
            ResourcePriceVersion.model_validate(row.data)
            for row in await self._resources.prices.list()
        )

    async def list_exchange_rates(self) -> tuple[ExchangeRateVersion, ...]:
        return tuple(
            ExchangeRateVersion.model_validate(row.data)
            for row in await self._resources.rates.list()
        )

    async def list_reconciliations(self, principal_id: str) -> tuple[InvoiceReconciliation, ...]:
        data = await self._ledger.read(principal_id)
        return tuple(
            InvoiceReconciliation.model_validate(item) for item in data.get("invoices", {}).values()
        )

    async def set_budget(self, limit: BudgetLimit) -> BudgetStatus:
        self._validate_currency(limit.currency)

        def update(data: dict[str, Any]) -> BudgetStatus:
            data["limits"][limit.currency] = str(limit.amount)
            return budget_status(data, limit.principal_id, limit.currency)

        return await self._ledger.mutate(limit.principal_id, update)

    async def budget(self, principal_id: str, currency: str) -> BudgetStatus:
        self._validate_currency(currency)
        return budget_status(await self._ledger.read(principal_id), principal_id, currency)

    async def record(self, fact: UsageFact) -> CostRecord:
        price = await self._prices.resolve(fact)
        # Cumulative deltas are determined under CAS; retain an FX multiplier for that delta.
        reference = price_fact(fact, price)
        if reference.amount is not None:
            reference = reference.model_copy(update={"amount": Decimal(1)})
        unit_cost = await self._resources.convert(reference, fact.occurred_at)

        def update(data: dict[str, Any]) -> CostRecord:
            existing = prior_observation(data, fact)
            if existing is not None:
                return existing
            delta = usage_delta(data, fact)
            cost = (
                price_fact(delta, price)
                if delta is not None
                else CostRecord(
                    call_id=fact.call_id,
                    principal_id=fact.principal_id,
                    run_id=fact.run_id,
                    model_profile=fact.model_profile,
                    status="ignored",
                )
            )
            if (
                delta is not None
                and cost.amount is not None
                and unit_cost.currency != cost.currency
            ):
                multiplier = (
                    unit_cost.amount / unit_cost.source_amount
                    if unit_cost.amount is not None and unit_cost.source_amount
                    else None
                )
                cost = cost.model_copy(
                    update={
                        "source_amount": cost.amount,
                        "source_currency": cost.currency,
                        "amount": cost.amount * multiplier if multiplier is not None else None,
                        "currency": unit_cost.currency,
                        "status": unit_cost.status,
                        "exchange_rate_id": unit_cost.exchange_rate_id,
                    }
                )
            issue = apply_cost(data, cost, fact.reservation_id)
            if issue:
                cost = cost.model_copy(update={"reservation_issue": issue})
            data["observations"][fact.call_id] = {
                "fact": fact.model_dump(mode="json"),
                "priced_fact": delta.model_dump(mode="json")
                if delta
                else fact.model_dump(mode="json"),
                "cost": cost.model_dump(mode="json"),
            }
            return cost

        return await self._ledger.mutate(fact.principal_id, update)

    async def publish_resource_price(self, price: ResourcePriceVersion) -> ResourcePriceVersion:
        return await self._resources.publish_price(price)

    async def publish_exchange_rate(self, rate: ExchangeRateVersion) -> ExchangeRateVersion:
        return await self._resources.publish_rate(rate)

    async def record_resource(self, fact: ResourceUsageFact) -> CostRecord:
        cost = await self._resources.price(fact)

        def update(data: dict[str, Any]) -> CostRecord:
            prior = data["observations"].get(fact.call_id)
            if prior is not None:
                incoming = fact.model_dump(mode="json", exclude={"occurred_at"})
                existing = {k: v for k, v in prior["fact"].items() if k != "occurred_at"}
                if prior.get("type") != "resource" or incoming != existing:
                    raise OpsMeshError("CONFLICT", "Resource usage identity has different values")
                return CostRecord.model_validate(prior["cost"])
            issue = apply_cost(data, cost, fact.reservation_id)
            recorded = cost.model_copy(update={"reservation_issue": issue})
            data["observations"][fact.call_id] = {
                "type": "resource",
                "fact": fact.model_dump(mode="json"),
                "cost": recorded.model_dump(mode="json"),
            }
            return recorded

        return await self._ledger.mutate(fact.principal_id, update)

    async def reprice(self, principal_id: str) -> tuple[CostRecord, ...]:
        snapshot = await self._ledger.read(principal_id)
        candidates: dict[str, CostRecord] = {}
        for identity, item in snapshot["observations"].items():
            if item["cost"]["status"] != "unpriced":
                continue
            if item.get("type") == "resource":
                cost = await self._resources.price(ResourceUsageFact.model_validate(item["fact"]))
            else:
                measured = dict(item["fact"])
                for counter in (
                    "input_tokens",
                    "output_tokens",
                    "cached_input_tokens",
                    "reasoning_tokens",
                ):
                    measured[counter] = item["cost"].get(counter, 0)
                fact = UsageFact.model_validate(item.get("priced_fact", measured))
                cost = await self._resources.convert(
                    price_fact(fact, await self._prices.resolve(fact)), fact.occurred_at
                )
            if cost.status == "priced":
                candidates[identity] = cost

        def update(data: dict[str, Any]) -> tuple[CostRecord, ...]:
            changed: list[CostRecord] = []
            for identity, cost in candidates.items():
                item = data["observations"].get(identity)
                if item is None or item["cost"]["status"] != "unpriced":
                    continue
                issue = apply_cost(data, cost, item["fact"].get("reservation_id"))
                recorded = cost.model_copy(update={"reservation_issue": issue})
                item["cost"] = recorded.model_dump(mode="json")
                changed.append(recorded)
            self._settle_repriced_reservations(data)
            return tuple(changed)

        return await self._ledger.mutate(principal_id, update)

    @staticmethod
    def _settle_repriced_reservations(data: dict[str, Any]) -> None:
        for identity, reservation in data["reservations"].items():
            if reservation["status"] != "pending_pricing":
                continue
            associated = [
                item["cost"]
                for item in data["observations"].values()
                if item["fact"].get("reservation_id") == identity
            ]
            billable = [cost for cost in associated if cost["status"] != "ignored"]
            if not associated or any(
                cost["amount"] is None or cost["currency"] != reservation["currency"]
                for cost in billable
            ):
                continue
            actual = sum((Decimal(cost["amount"]) for cost in billable), Decimal(0))
            settle_budget(data, identity, actual)

    async def reconcile(self, line: ProviderInvoiceLine) -> InvoiceReconciliation:
        return await self._ledger.mutate(line.principal_id, lambda data: reconcile_line(data, line))

    async def has_request_usage(self, principal_id: str, run_id: str) -> bool:
        data = await self._ledger.read(principal_id)
        return bool(data["runs"].get(run_id, {}).get("kind") == "request")

    async def costs(self, principal_id: str, run_id: str | None = None) -> tuple[CostRecord, ...]:
        data = await self._ledger.read(principal_id)
        return tuple(
            CostRecord.model_validate(item["cost"])
            for item in data["observations"].values()
            if run_id is None or item["cost"]["run_id"] == run_id
        )

    async def reserve(
        self, principal_id: str, reservation_id: str, amount: Decimal, currency: str
    ) -> BudgetReservation:
        self._validate_currency(currency)
        return await self._ledger.mutate(
            principal_id,
            lambda data: reserve_budget(data, principal_id, reservation_id, amount, currency),
        )

    async def settle(
        self, principal_id: str, reservation_id: str, actual: Decimal | None
    ) -> BudgetReservation:
        return await self._ledger.mutate(
            principal_id, lambda data: settle_budget(data, reservation_id, actual)
        )

    @staticmethod
    def _validate_currency(currency: str) -> None:
        if (
            len(currency) != 3
            or not currency.isascii()
            or not currency.isalpha()
            or not currency.isupper()
        ):
            raise OpsMeshError("CONFIG_INVALID", "Currency must be a three-letter uppercase code")
