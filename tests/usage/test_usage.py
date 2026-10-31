"""Billing counters, idempotency and reservation concurrency without external APIs."""

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.usage.api import BudgetLimit, PriceVersion, UsageFact
from opsmesh.modules.usage.internal.service import PersistentUsageService


@pytest_asyncio.fixture
async def usage(tmp_path: Path) -> AsyncIterator[PersistentUsageService]:
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'usage.sqlite'}")
    service = PersistentUsageService(database)
    await database.initialize()
    await service.publish_price(
        PriceVersion(
            id="price1",
            model_profile="model@1",
            currency="USD",
            input_per_million=Decimal("3"),
            output_per_million=Decimal("6"),
            cached_input_per_million=Decimal("0.5"),
        )
    )
    yield service
    await database.close()


def fact(call_id: str, **overrides: object) -> UsageFact:
    return UsageFact.model_validate(
        {
            "call_id": call_id,
            "principal_id": "alice",
            "run_id": "r1",
            "model_profile": "model@1",
            "input_tokens": 1_000_000,
            **overrides,
        }
    )


@pytest.mark.asyncio
async def test_exact_decimal_price_and_request_idempotency(usage: PersistentUsageService) -> None:
    request = fact("call1", cached_input_tokens=500_000, output_tokens=100_000)
    first = await usage.record(request)
    assert first.amount == Decimal("2.35")
    assert first.price_version_id == "price1"
    assert await usage.record(request) == first
    assert len(await usage.costs("alice")) == 1
    assert (await usage.budget("alice", "USD")).spent == Decimal("2.35")
    with pytest.raises(OpsMeshError, match="different counters"):
        await usage.record(request.model_copy(update={"output_tokens": 200_000}))


@pytest.mark.asyncio
async def test_unpriced_and_unavailable_are_not_zero_cost(usage: PersistentUsageService) -> None:
    unpriced = await usage.record(fact("unknown-model", model_profile="other@1"))
    unavailable = await usage.record(fact("unknown-usage", usage_available=False, input_tokens=0))
    assert unpriced.status == "unpriced" and unpriced.amount is None
    assert unavailable.status == "unavailable" and unavailable.amount is None
    status = await usage.budget("alice", "USD")
    assert status.unpriced_observations == 2 and status.limit is None


@pytest.mark.asyncio
async def test_pause_resume_cumulative_snapshots_book_deltas(usage: PersistentUsageService) -> None:
    first = await usage.record(fact("pause1", kind="cumulative", sequence=1))
    second = await usage.record(
        fact("resume1", kind="cumulative", sequence=2, input_tokens=1_500_000)
    )
    duplicate = await usage.record(
        fact("resume1-copy", kind="cumulative", sequence=2, input_tokens=1_500_000)
    )
    older = await usage.record(fact("old", kind="cumulative", sequence=0, input_tokens=10))
    assert first.amount == Decimal("3") and second.amount == Decimal("1.5")
    assert duplicate.status == older.status == "ignored"
    assert (await usage.budget("alice", "USD")).spent == Decimal("4.5")
    with pytest.raises(OpsMeshError, match="inconsistent"):
        await usage.record(fact("regression", kind="cumulative", sequence=3, input_tokens=1))


@pytest.mark.asyncio
async def test_cumulative_cannot_double_bill_request_or_nested_runs(
    usage: PersistentUsageService,
) -> None:
    await usage.record(fact("direct"))
    assert await usage.has_request_usage("alice", "r1")
    with pytest.raises(OpsMeshError, match="cannot be billed together"):
        await usage.record(fact("aggregate", kind="cumulative", sequence=1))
    await usage.record(fact("child", run_id="child", parent_run_id="parent"))
    with pytest.raises(OpsMeshError, match="Child runs"):
        await usage.record(fact("parent", run_id="parent", kind="cumulative", sequence=1))
    await usage.record(fact("other-parent", run_id="other", kind="cumulative", sequence=1))
    with pytest.raises(OpsMeshError, match="parent aggregate"):
        await usage.record(fact("other-child", run_id="other-child", parent_run_id="other"))


@pytest.mark.asyncio
async def test_budget_reserve_consume_and_settle_do_not_double_book(
    usage: PersistentUsageService,
) -> None:
    await usage.set_budget(BudgetLimit(principal_id="alice", currency="USD", amount=Decimal("10")))
    reservation = await usage.reserve("alice", "r1", Decimal("5"), "USD")
    assert await usage.reserve("alice", "r1", Decimal("5"), "USD") == reservation
    await usage.record(fact("call", reservation_id="r1"))
    interim = await usage.budget("alice", "USD")
    assert interim.spent == Decimal("3") and interim.reserved == Decimal("2")
    settled = await usage.settle("alice", "r1", Decimal("3"))
    assert settled.remaining == 0
    status = await usage.budget("alice", "USD")
    assert status.spent == Decimal("3") and status.available == Decimal("7")
    assert await usage.settle("alice", "r1", Decimal("3")) == settled
    with pytest.raises(OpsMeshError):
        await usage.settle("alice", "r1", Decimal("4"))


@pytest.mark.asyncio
async def test_unknown_actual_retains_reservation(usage: PersistentUsageService) -> None:
    await usage.reserve("alice", "r1", Decimal("5"), "USD")
    await usage.record(fact("unknown", model_profile="unknown", reservation_id="r1"))
    reservation = await usage.settle("alice", "r1", None)
    assert reservation.status == "pending_pricing" and reservation.remaining == Decimal("5")


@pytest.mark.asyncio
async def test_concurrent_reservations_cannot_overspend(usage: PersistentUsageService) -> None:
    await usage.set_budget(BudgetLimit(principal_id="alice", currency="USD", amount=Decimal("10")))
    results = await asyncio.gather(
        usage.reserve("alice", "one", Decimal("7"), "USD"),
        usage.reserve("alice", "two", Decimal("7"), "USD"),
        return_exceptions=True,
    )
    assert sum(isinstance(item, OpsMeshError) for item in results) == 1
    assert (await usage.budget("alice", "USD")).reserved == Decimal("7")


@pytest.mark.asyncio
async def test_concurrent_requests_and_replays_have_no_lost_updates(
    usage: PersistentUsageService,
) -> None:
    requests = [fact(f"call-{index}", input_tokens=100) for index in range(10)]
    await asyncio.gather(*(usage.record(request) for request in requests))
    await asyncio.gather(*(usage.record(request) for request in requests))
    assert len(await usage.costs("alice")) == 10
    assert (await usage.budget("alice", "USD")).spent == Decimal("0.003")


@pytest.mark.asyncio
async def test_consumed_overrun_is_recorded_and_blocks_new_reservation(
    usage: PersistentUsageService,
) -> None:
    await usage.set_budget(BudgetLimit(principal_id="alice", currency="USD", amount=Decimal("2")))
    await usage.reserve("alice", "r1", Decimal("1"), "USD")
    cost = await usage.record(fact("already-consumed", reservation_id="r1"))
    assert cost.amount == Decimal("3")
    assert (await usage.budget("alice", "USD")).available == Decimal("-1")
    with pytest.raises(OpsMeshError, match="Insufficient"):
        await usage.reserve("alice", "next", Decimal("0.1"), "USD")
