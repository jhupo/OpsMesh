"""Decimal pricing with explicit unavailable and unpriced states."""

from datetime import UTC
from decimal import Decimal
from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.usage.api import CostRecord, PriceVersion, UsageFact


class VersionedPrices:
    def __init__(self, database: AsyncDatabase) -> None:
        self._prices = VersionedRecordStore(database, "usage_prices")

    async def publish(self, price: PriceVersion) -> PriceVersion:
        if not price.id or not price.model_profile:
            raise OpsMeshError("CONFIG_INVALID", "Price identity and model profile are required")
        prior = await self._prices.get(price.id)
        if prior is not None:
            if prior.data != price.model_dump(mode="json"):
                raise OpsMeshError("CONFLICT", "Price versions are immutable")
            return PriceVersion.model_validate(prior.data)
        await self._prices.create(price.id, price.model_dump(mode="json"))
        return price

    async def resolve(self, fact: UsageFact) -> PriceVersion | None:
        candidates = [PriceVersion.model_validate(row.data) for row in await self._prices.list()]
        candidates = [
            price
            for price in candidates
            if price.model_profile == fact.model_profile
            and price.effective_at.replace(tzinfo=price.effective_at.tzinfo or UTC)
            <= fact.occurred_at.replace(tzinfo=fact.occurred_at.tzinfo or UTC)
        ]
        return max(
            candidates,
            key=lambda price: (
                price.effective_at.replace(tzinfo=price.effective_at.tzinfo or UTC),
                price.id,
            ),
            default=None,
        )

    async def list(self) -> tuple[PriceVersion, ...]:
        return tuple(PriceVersion.model_validate(row.data) for row in await self._prices.list())


def price_fact(fact: UsageFact, price: PriceVersion | None) -> CostRecord:
    values: dict[str, Any] = dict(
        call_id=fact.call_id,
        principal_id=fact.principal_id,
        run_id=fact.run_id,
        model_profile=fact.model_profile,
        input_tokens=fact.input_tokens,
        output_tokens=fact.output_tokens,
        cached_input_tokens=fact.cached_input_tokens,
        reasoning_tokens=fact.reasoning_tokens,
    )
    if not fact.usage_available:
        return CostRecord(**values, status="unavailable")
    if price is None:
        return CostRecord(**values, status="unpriced")
    if fact.cached_input_tokens and price.cached_input_per_million is None:
        return CostRecord(
            **values, status="unpriced", currency=price.currency, price_version_id=price.id
        )
    amount = (
        Decimal(fact.input_tokens - fact.cached_input_tokens) * price.input_per_million
        + Decimal(fact.cached_input_tokens) * (price.cached_input_per_million or Decimal(0))
        + Decimal(fact.output_tokens) * price.output_per_million
    ) / Decimal(1_000_000)
    return CostRecord(
        **values, status="priced", amount=amount, currency=price.currency, price_version_id=price.id
    )
