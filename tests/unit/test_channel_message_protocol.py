"""Verify shared structured replies and bounded bot message consumption."""

from types import SimpleNamespace

import anyio
import pytest

from opsmesh.plugins.channels import (
    AttachmentCollection,
    Message,
    MessageSegment,
)
from opsmesh.plugins.channels._queue import EventQueue
from opsmesh.plugins.channels.dingtalk.adapter import DingTalkAdapter
from opsmesh.plugins.channels.dingtalk.events import DingTalkMessageEvent
from opsmesh.plugins.channels.qq.adapter import QQAdapter
from opsmesh.plugins.channels.qq.events import QQMessageEvent
from opsmesh.plugins.channels.sending import aggregate_stream, prepare


@pytest.mark.anyio
async def test_bot_output_omits_structured_data_without_turning_it_into_text():
    message = Message(
        [
            MessageSegment.text("Selected session"),
            MessageSegment.structured({"session_id": "session-123"}),
        ]
    )
    assert await prepare(message, AttachmentCollection(), 2000) == [
        "Selected session",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "segments",
    [
        [MessageSegment.structured({"result": 1})],
        [
            MessageSegment.text("Result"),
            MessageSegment.structured({}),
            MessageSegment.structured({}),
        ],
    ],
)
async def test_structured_output_requires_readable_content_and_one_data_segment(
    segments,
):
    with pytest.raises(ValueError):
        await prepare(Message(segments), AttachmentCollection(), 2000)


@pytest.mark.parametrize(
    "value",
    [[], "text", object(), {"value": object()}, {"value": float("nan")}],
)
def test_structured_factory_rejects_non_json_objects(value):
    with pytest.raises((TypeError, ValueError)):
        MessageSegment.structured(value)


def test_structured_data_has_a_shared_size_limit(monkeypatch):
    from opsmesh.plugins.channels import messages

    monkeypatch.setattr(messages, "MAX_STRUCTURED_BYTES", 8)
    with pytest.raises(ValueError, match="size limit"):
        MessageSegment.structured({"large": "value"})


@pytest.mark.anyio
@pytest.mark.parametrize("protocol", ["qq", "dingtalk"])
async def test_bot_stream_consumes_the_source_before_one_complete_send(
    protocol,
):
    if protocol == "qq":
        adapter = object.__new__(QQAdapter)
        event = QQMessageEvent(
            connection_id="bot",
            user_id="user",
            message_id="message",
            event_id="event",
            content="hello",
            raw={},
        )
    else:
        adapter = object.__new__(DingTalkAdapter)
        event = DingTalkMessageEvent(
            connection_id="bot",
            user_id="user",
            message_id="message",
            conversation_id="conversation",
            content="hello",
            raw={},
        )
    channel = SimpleNamespace(self_id="bot", ready=True)
    adapter._declared = {"bot": channel}
    stages = []

    async def source():
        try:
            yield "one"
            assert stages == []
            yield " two"
        finally:
            stages.append("source closed")

    async def send(channel, event, message):
        assert stages == ["source closed"]
        stages.append(message)

    adapter._send_message = send
    await adapter._call(channel, "stream", event=event, source=source())
    assert stages == ["source closed", "one two"]


@pytest.mark.anyio
async def test_stream_aggregation_closes_the_source_when_size_limit_is_exceeded(
    monkeypatch,
):
    from opsmesh.plugins.channels import sending

    monkeypatch.setattr(sending, "MAX_STREAM_BYTES", 5)
    closed = []

    async def source():
        try:
            yield "first"
            yield "second"
        finally:
            closed.append(True)

    with pytest.raises(ValueError, match="size limit"):
        await aggregate_stream(source())
    assert closed == [True]


@pytest.mark.anyio
async def test_bot_queue_serializes_each_scope_while_other_scopes_progress():
    entered, release, other = anyio.Event(), anyio.Event(), anyio.Event()
    stages = []

    async def handle(item):
        stages.append(("start", item["number"]))
        if item["number"] == 1:
            entered.set()
            await release.wait()
        if item["scope"] == "other":
            other.set()
        stages.append(("end", item["number"]))

    queue = EventQueue(
        capacity=4,
        concurrency=3,
        shutdown_timeout=1,
        handle=handle,
        scope=lambda item: item["scope"],
    )
    async with anyio.create_task_group() as tasks:
        await queue.start(tasks)
        assert queue.put_nowait({"scope": "same", "number": 1})
        assert queue.put_nowait({"scope": "same", "number": 2})
        assert queue.put_nowait({"scope": "other", "number": 3})
        await entered.wait()
        await other.wait()
        assert ("start", 2) not in stages
        release.set()
        await queue.stop()
    assert stages.index(("end", 1)) < stages.index(("start", 2))
    assert not queue._lanes


@pytest.mark.anyio
async def test_incomplete_queue_stop_retains_workers_until_a_successful_retry():
    entered, release = anyio.Event(), anyio.Event()

    async def handle(item):
        with anyio.CancelScope(shield=True):
            entered.set()
            await release.wait()

    queue = EventQueue(
        capacity=1,
        concurrency=1,
        shutdown_timeout=0.01,
        handle=handle,
    )
    async with anyio.create_task_group() as tasks:
        await queue.start(tasks)
        assert queue.put_nowait("event")
        await entered.wait()
        with pytest.raises(TimeoutError):
            await queue.stop()
        assert queue._task_group is not None
        assert queue._receive is not None
        assert not queue.put_nowait("new")
        with pytest.raises(RuntimeError, match="has not stopped"):
            await queue.start(tasks)
        release.set()
        await queue.stop()
    assert queue._task_group is None
    assert queue._receive is None
