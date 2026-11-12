from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest

from opsmesh.dispatch import SessionBusyError, SessionGuard


@pytest.fixture
def channel():
    return SimpleNamespace(type="test", self_id="connection")


def event(session="conversation"):
    return SimpleNamespace(get_session_id=lambda: session)


@pytest.mark.anyio
@pytest.mark.parametrize("same_event", [True, False])
async def test_overlap_is_rejected_and_slot_is_available_after_completion(
    channel, same_event
):
    entered, release = anyio.Event(), anyio.Event()
    calls = []

    async def handler(channel, incoming):
        calls.append(incoming)
        entered.set()
        await release.wait()

    guard = SessionGuard(handler)
    first = event()
    second = first if same_event else event()
    with anyio.fail_after(2):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(guard, channel, first)
            await entered.wait()
            try:
                with pytest.raises(SessionBusyError):
                    await guard(channel, second)
            finally:
                release.set()
        await guard(channel, second)
    assert calls == [first, second]


@pytest.mark.anyio
async def test_handler_failure_releases_slot(channel):
    handler = AsyncMock(side_effect=[RuntimeError("Failed"), None])
    guard = SessionGuard(handler)
    with pytest.raises(RuntimeError, match="Failed"):
        await guard(channel, event())
    await guard(channel, event())
    assert handler.await_count == 2


@pytest.mark.anyio
async def test_cancellation_releases_slot(channel):
    async def cancelled(channel, incoming):
        await anyio.sleep(0)

    handler = AsyncMock(side_effect=cancelled)
    guard = SessionGuard(handler)
    with anyio.CancelScope() as scope:
        scope.cancel()
        await guard(channel, event())
    await guard(channel, event())
    assert handler.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    "scope", ["session", "connection", "provider", "owner"]
)
async def test_independent_scopes_can_overlap(channel, scope):
    entered, release = anyio.Event(), anyio.Event()
    calls = []

    async def handler(current, incoming):
        calls.append((current, incoming))
        if len(calls) == 1:
            entered.set()
            await release.wait()

    guard = SessionGuard(handler)
    other = SimpleNamespace(**vars(channel))
    second = event("other" if scope == "session" else "conversation")
    if scope == "connection":
        other.self_id = "other"
    if scope == "provider":
        other.type = "other"
    second_guard = SessionGuard(handler) if scope == "owner" else guard
    with anyio.fail_after(2):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(guard, channel, event())
            await entered.wait()
            try:
                await second_guard(other, second)
            finally:
                release.set()
    assert len(calls) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("error", [ValueError, NotImplementedError])
async def test_events_without_session_are_processed(channel, error):
    def no_session():
        raise error("No session")

    handler = AsyncMock()
    incoming = SimpleNamespace(get_session_id=no_session)
    await SessionGuard(handler)(channel, incoming)
    handler.assert_awaited_once_with(channel, incoming)


@pytest.mark.anyio
async def test_unexpected_identity_error_is_not_ignored(channel):
    def broken():
        raise RuntimeError("Identity unavailable")

    handler = AsyncMock()
    with pytest.raises(RuntimeError, match="Identity unavailable"):
        await SessionGuard(handler)(
            channel, SimpleNamespace(get_session_id=broken)
        )
    handler.assert_not_awaited()
