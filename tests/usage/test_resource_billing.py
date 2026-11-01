"""Resource costs and currency conversion retain immutable, reconcilable facts."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.usage.api import (
    BudgetLimit,
    ExchangeRateVersion,
    PriceVersion,
    ProviderInvoiceLine,
    ResourcePriceVersion,
    ResourceUsageFact,
    UsageFact,
)
from opsmesh.modules.usage.internal.service import PersistentUsageService


async def test_resource_currency_repricing_and_invoice_do_not_double_bill(tmp_path: Path) -> None:
    database = AsyncDatabase(f"sqlite:///{tmp_path / 'billing.db'}")
    usage = PersistentUsageService(database, billing_currency="USD")
    await database.initialize()
    before = datetime.now(UTC) - timedelta(days=1)
    try:
        await usage.publish_resource_price(
            ResourcePriceVersion(
                id="compute-v1",
                resource_key="sandbox:cpu",
                unit="cpu_second",
                currency="EUR",
                amount_per_unit=Decimal("0.02"),
                effective_at=before,
            )
        )
        fact = ResourceUsageFact(
            call_id="cpu1",
            principal_id="alice",
            run_id="r",
            resource_key="sandbox:cpu",
            unit="cpu_second",
            quantity=Decimal(30),
        )
        cost = await usage.record_resource(fact)
        assert cost.status == "unpriced" and cost.amount is None
        assert cost.source_amount == Decimal("0.60") and cost.currency == "USD"
        await usage.publish_exchange_rate(
            ExchangeRateVersion(
                id="fx1",
                base_currency="EUR",
                quote_currency="USD",
                rate=Decimal("1.1"),
                effective_at=before,
            )
        )
        repriced = await asyncio.gather(usage.reprice("alice"), usage.reprice("alice"))
        assert sum(len(items) for items in repriced) == 1
        assert (await usage.budget("alice", "USD")).spent == Decimal("0.66")
        assert (await usage.record_resource(fact)).exchange_rate_id == "fx1"
        line = ProviderInvoiceLine(
            provider="sandbox",
            invoice_id="invoice1",
            line_id="line1",
            principal_id="alice",
            call_id="cpu1",
            amount=Decimal("0.60"),
            currency="EUR",
        )
        assert (await usage.reconcile(line)).status == "matched"
        assert (await usage.reconcile(line)).difference == 0
        assert (await usage.budget("alice", "USD")).spent == Decimal("0.66")
        with pytest.raises(OpsMeshError, match="different values"):
            await usage.reconcile(line.model_copy(update={"amount": Decimal("2")}))
        with pytest.raises(OpsMeshError, match="different values"):
            await usage.record_resource(fact.model_copy(update={"quantity": Decimal(31)}))
    finally:
        await database.close()


async def test_unpriced_cumulative_reprices_delta_not_full_snapshot(tmp_path: Path) -> None:
    database = AsyncDatabase(f"sqlite:///{tmp_path / 'cumulative.db'}")
    usage = PersistentUsageService(database, billing_currency="USD")
    await database.initialize()
    before = datetime.now(UTC) - timedelta(days=1)
    try:
        common = dict(principal_id="a", run_id="r", model_profile="m", kind="cumulative")
        await usage.record(
            UsageFact.model_validate(dict(common, call_id="one", input_tokens=100, sequence=1))
        )
        await usage.record(
            UsageFact.model_validate(dict(common, call_id="two", input_tokens=150, sequence=2))
        )
        await usage.publish_price(
            PriceVersion(
                id="p",
                model_profile="m",
                currency="EUR",
                input_per_million=Decimal(1_000_000),
                output_per_million=Decimal(0),
                effective_at=before,
            )
        )
        await usage.publish_exchange_rate(
            ExchangeRateVersion(
                id="fx",
                base_currency="EUR",
                quote_currency="USD",
                rate=Decimal(2),
                effective_at=before,
            )
        )
        costs = await usage.reprice("a")
        assert [cost.amount for cost in costs] == [Decimal(200), Decimal(100)]
        assert (await usage.budget("a", "USD")).spent == Decimal(300)
        zero = await usage.record(
            UsageFact.model_validate(dict(common, call_id="three", input_tokens=150, sequence=3))
        )
        assert zero.status == "priced" and zero.amount == 0
        assert zero.source_amount == 0
    finally:
        await database.close()


async def test_unknown_resource_measurement_stays_unavailable(tmp_path: Path) -> None:
    database = AsyncDatabase(f"sqlite:///{tmp_path / 'unknown.db'}")
    usage = PersistentUsageService(database)
    await database.initialize()
    try:
        fact = ResourceUsageFact(
            call_id="unknown",
            principal_id="a",
            run_id="r",
            resource_key="image:tokens",
            unit="token",
            quantity=Decimal(0),
            usage_available=False,
        )
        assert (await usage.record_resource(fact)).status == "unavailable"
        assert await usage.reprice("a") == ()
        line = ProviderInvoiceLine(
            provider="p",
            invoice_id="i",
            line_id="l",
            principal_id="a",
            call_id="unknown",
            amount=Decimal(1),
            currency="USD",
        )
        assert (await usage.reconcile(line)).status == "unpriced"
    finally:
        await database.close()


async def test_reprice_releases_only_completed_fully_priced_reservations(tmp_path: Path) -> None:
    database = AsyncDatabase(f"sqlite:///{tmp_path / 'reservations.db'}")
    usage = PersistentUsageService(database, billing_currency="USD")
    await database.initialize()
    try:
        await usage.set_budget(BudgetLimit(principal_id="a", currency="USD", amount=Decimal(100)))
        for run in ("done", "running", "partial"):
            await usage.reserve("a", run, Decimal(10), "USD")
            await usage.record(
                UsageFact(
                    call_id=f"{run}:model",
                    principal_id="a",
                    run_id=run,
                    model_profile="model",
                    input_tokens=2,
                    reservation_id=run,
                )
            )
        await usage.record_resource(
            ResourceUsageFact(
                call_id="partial:unknown",
                principal_id="a",
                run_id="partial",
                resource_key="hosted:image_generation",
                unit="call",
                quantity=Decimal(0),
                usage_available=False,
                reservation_id="partial",
            )
        )
        await usage.settle("a", "done", None)
        await usage.settle("a", "partial", None)
        await usage.publish_price(
            PriceVersion(
                id="price",
                model_profile="model",
                currency="USD",
                input_per_million=Decimal(1_000_000),
                output_per_million=Decimal(0),
                effective_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        changes = await asyncio.gather(usage.reprice("a"), usage.reprice("a"))
        assert sum(len(items) for items in changes) == 3
        completed = await usage.reserve("a", "done", Decimal(10), "USD")
        assert completed.status == "settled" and completed.actual == 2
        assert completed.remaining == 0
        running = await usage.reserve("a", "running", Decimal(10), "USD")
        assert running.status == "reserved" and running.remaining == 8
        partial = await usage.reserve("a", "partial", Decimal(10), "USD")
        assert partial.status == "pending_pricing" and partial.remaining == 8
        status = await usage.budget("a", "USD")
        assert status.spent == 6 and status.reserved == 16 and status.available == 78
        assert await usage.reprice("a") == ()
        assert await usage.budget("a", "USD") == status
    finally:
        await database.close()
