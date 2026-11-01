import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

import pytest

from opsmesh.framework import on_command, on_event, on_message
from opsmesh.framework.adapters import (
    MessageEvent,
    OpsMeshAdapter,
    OpsMeshBot,
    OpsMeshEvent,
    OpsMeshMessage,
    OpsMeshMessageSegment,
)
from opsmesh.framework.dependencies import Depends
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.events import DispatchContext, EventEnvelope, get_current_context
from opsmesh.framework.exception import ContextUnavailableError, ReplyUnavailableError
from opsmesh.framework.matcher import OpsMeshMatcher, get_current_matcher
from opsmesh.framework.message import handle_event
from opsmesh.framework.params import CommandArg


def message(text: str, session: str = "conversation") -> MessageEvent:
    return MessageEvent(message=OpsMeshMessage(text), user_id="user", session_id=session)


def test_message_and_envelope_roundtrip_and_identity_validation() -> None:
    original = message("hello")
    original = original.model_copy(
        update={"message": original.message + OpsMeshMessageSegment.file("file:1")}
    )
    envelope = EventEnvelope.from_event(original, correlation_id="request")
    restored = EventEnvelope.model_validate_json(envelope.model_dump_json()).decode(MessageEvent)
    assert restored == original
    assert restored.get_plaintext() == "hello"
    bad = envelope.model_copy(update={"id": "other"})
    with pytest.raises(ValueError, match="identity mismatch"):
        bad.decode(MessageEvent)


@pytest.mark.asyncio
async def test_botless_event_skips_bot_handler_but_runs_generic_handler() -> None:
    bot_handler = on_event("run.completed", priority=1)
    generic = on_event(OpsMeshEvent, priority=2)
    seen: list[str] = []

    @bot_handler.handle()
    async def need_bot(bot: OpsMeshBot) -> None:
        seen.append(bot.self_id)

    @generic.handle()
    async def receive(event: OpsMeshEvent, bot: OpsMeshBot | None) -> None:
        assert bot is None
        seen.append(event.type)
        await generic.finish()

    result = await handle_event(OpsMeshEvent(type="run.completed"), raise_on_error=True)
    assert result.matched == 1
    assert seen == ["run.completed"]
    with pytest.raises(ContextUnavailableError):
        get_current_context()
    with pytest.raises(ContextUnavailableError):
        get_current_matcher()


@pytest.mark.asyncio
async def test_same_priority_runs_concurrently_and_block_preserves_peers() -> None:
    first = on_event("ready", priority=1, block=True)
    second = on_event("ready", priority=1)
    later = on_event("ready", priority=2)
    arrived = asyncio.Event()
    seen: list[str] = []

    @first.handle()
    async def a() -> None:
        await asyncio.wait_for(arrived.wait(), 2)
        seen.append("first")

    @second.handle()
    async def b() -> None:
        arrived.set()
        seen.append("second")

    @later.handle()
    async def c() -> None:
        seen.append("later")

    result = await handle_event(OpsMeshEvent(type="ready"), raise_on_error=True)
    assert set(seen) == {"first", "second"}
    assert result.matched == 2 and result.stopped


@pytest.mark.asyncio
async def test_command_boundaries_and_argument_injection() -> None:
    matcher = on_command("run")
    seen: list[str] = []

    @matcher.handle()
    async def handler(argument: Annotated[OpsMeshMessage, CommandArg()]) -> None:
        seen.append(argument.extract_plain_text())

    for event in (OpsMeshEvent(type="timer"), message("/runner abc"), message("/run abc")):
        await handle_event(event, raise_on_error=True)
    assert seen == ["abc"]


@pytest.mark.asyncio
async def test_pause_and_reject_resume_only_matching_session() -> None:
    matcher = on_command("form")
    collected: list[str] = []

    @matcher.handle()
    async def begin() -> None:
        matcher.state["began"] = True
        await matcher.pause()

    @matcher.handle()
    async def answer(event: MessageEvent) -> None:
        assert matcher.state["began"]
        if event.get_plaintext() == "bad":
            await matcher.reject()
        collected.append(event.get_plaintext())
        await matcher.finish()

    for event in (message("/form"), message("ignored", "other"), message("bad"), message("good")):
        await handle_event(event, raise_on_error=True)
    assert collected == ["good"]
    assert not (await handle_event(message("extra"))).matched


@pytest.mark.asyncio
async def test_concurrent_events_have_isolated_state_and_context() -> None:
    matcher = on_message(state={"values": []})
    seen: list[tuple[str, list[str]]] = []

    @matcher.handle()
    async def receive(event: MessageEvent, matcher: OpsMeshMatcher) -> None:
        matcher.state["values"].append(event.session_id)
        await asyncio.sleep(0)
        assert get_current_context().event is event
        seen.append((event.session_id, list(matcher.state["values"])))

    await asyncio.gather(handle_event(message("a", "a")), handle_event(message("b", "b")))
    assert sorted(seen) == [("a", ["a"]), ("b", ["b"])]


@pytest.mark.asyncio
async def test_errors_are_reported_and_dependencies_released() -> None:
    matcher = on_event("failure")
    closed: list[bool] = []

    async def resource() -> AsyncIterator[int]:
        try:
            yield 1
        finally:
            closed.append(True)

    @matcher.handle()
    async def handler(value: int = Depends(resource)) -> None:
        await matcher.finish("cannot send without bot")

    result = await handle_event(OpsMeshEvent(type="failure"))
    assert len(result.errors) == 1 and isinstance(result.errors[0], ReplyUnavailableError)
    assert closed == [True]


@pytest.mark.asyncio
async def test_adapter_bot_roundtrip_and_temp_matcher_executes_once() -> None:
    class TestBot(OpsMeshBot):
        async def send(self, target: object, message: OpsMeshMessage) -> str:
            sent.append(str(message))
            return "receipt"

        async def call_api(self, api: str, **data: object) -> object:
            return data

    sent: list[str] = []
    adapter = OpsMeshAdapter(OpsMeshDriver())
    bot = TestBot(adapter, "bot")
    adapter.connect(bot)
    matcher = on_message(temp=True)

    @matcher.handle()
    async def reply() -> None:
        await matcher.send("hello")

    await asyncio.gather(adapter.dispatch(message("a"), bot), adapter.dispatch(message("b"), bot))
    assert sent == ["hello"]
    assert adapter.disconnect("bot") is bot


@pytest.mark.asyncio
async def test_cancellation_is_not_a_handler_error() -> None:
    matcher = on_event("wait")
    entered = asyncio.Event()

    @matcher.handle()
    async def wait() -> None:
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(handle_event(DispatchContext(event=OpsMeshEvent(type="wait"))))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ContextUnavailableError):
        get_current_context()
