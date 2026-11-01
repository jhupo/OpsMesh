"""A waiting task does not acquire the right to cancel the durable Run."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from opsmesh.application.waiting import wait_for_run
from opsmesh.framework.agents import AgentSpecification
from opsmesh.modules.runtime.api import AgentRunView


def run(status):
    return AgentRunView(
        id="run",
        revision=1,
        principal_id="alice",
        session_id="group",
        agent=AgentSpecification(id="agent", name="Agent"),
        status=status,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "waiting_approval"])
async def test_wait_returns_attention_and_terminal_states(status):
    read = AsyncMock(side_effect=[run("running"), run(status)])
    assert (await wait_for_run(read, interval=0)).status == status
    assert read.await_count == 2


async def test_cancelling_waiter_preserves_durable_run():
    read = AsyncMock(return_value=run("running"))
    task = asyncio.create_task(wait_for_run(read, interval=10))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert read.await_count == 1
