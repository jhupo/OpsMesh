"""Versioned resource tariffs and explicit currency conversion."""

from datetime import UTC, datetime
from typing import Any, TypeVar

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.usage.api import (
    CostRecord,
    ExchangeRateVersion,
    ResourcePriceVersion,
    ResourceUsageFact,
)

Version = TypeVar("Version", bound=OpsMeshContract)


def instant(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or UTC)


async def publish_version(store: VersionedRecordStore, identity: str, value: Version) -> Version:
    data = value.model_dump(mode="json")
    prior = await store.get(identity)
    if prior is not None:
        if prior.data != data:
            raise OpsMeshError("CONFLICT", "Published billing versions are immutable")
        return value
    await store.create(identity, data)
    return value


class ResourcePricingCatalog:
    def __init__(self, database: AsyncDatabase, billing_currency: str | None) -> None:
        self.prices = VersionedRecordStore(database, "usage_resource_prices")
        self.rates = VersionedRecordStore(database, "usage_exchange_rates")
        self.billing_currency = billing_currency

    async def publish_price(self, price: ResourcePriceVersion) -> ResourcePriceVersion:
        return await publish_version(self.prices, price.id, price)

    async def publish_rate(self, rate: ExchangeRateVersion) -> ExchangeRateVersion:
        if rate.base_currency == rate.quote_currency and rate.rate != 1:
            raise OpsMeshError("CONFIG_INVALID", "Same-currency exchange rate must equal one")
        return await publish_version(self.rates, rate.id, rate)

    async def price(self, fact: ResourceUsageFact) -> CostRecord:
        values: dict[str, Any] = {
            "call_id": fact.call_id,
            "principal_id": fact.principal_id,
            "run_id": fact.run_id,
            "model_profile": f"resource:{fact.resource_key}",
            "resource_key": fact.resource_key,
            "unit": fact.unit,
            "quantity": fact.quantity,
        }
        if not fact.usage_available:
            return CostRecord(**values, status="unavailable")
        candidates = [
            ResourcePriceVersion.model_validate(row.data) for row in await self.prices.list()
        ]
        price = max(
            (
                item
                for item in candidates
                if item.resource_key == fact.resource_key
                and item.unit == fact.unit
                and instant(item.effective_at) <= instant(fact.occurred_at)
            ),
            key=lambda item: (instant(item.effective_at), item.id),
            default=None,
        )
        if price is None:
            return CostRecord(**values, status="unpriced")
        cost = CostRecord(
            **values,
            status="priced",
            amount=fact.quantity * price.amount_per_unit,
            currency=price.currency,
            price_version_id=price.id,
        )
        return await self.convert(cost, fact.occurred_at)

    async def convert(self, cost: CostRecord, occurred_at: datetime) -> CostRecord:
        target = self.billing_currency
        if cost.amount is None or not cost.currency or not target or cost.currency == target:
            return cost
        candidates = [
            ExchangeRateVersion.model_validate(row.data) for row in await self.rates.list()
        ]
        rate = max(
            (
                item
                for item in candidates
                if item.base_currency == cost.currency
                and item.quote_currency == target
                and instant(item.effective_at) <= instant(occurred_at)
            ),
            key=lambda item: (instant(item.effective_at), item.id),
            default=None,
        )
        return cost.model_copy(
            update={
                "source_amount": cost.amount,
                "source_currency": cost.currency,
                "currency": target,
                "amount": cost.amount * rate.rate if rate else None,
                "status": "priced" if rate else "unpriced",
                "exchange_rate_id": rate.id if rate else None,
            }
        )
