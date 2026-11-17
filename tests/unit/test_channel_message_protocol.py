"""Verify shared structured replies and bounded bot message consumption."""

from types import ModuleType

import anyio
import pytest

from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.plugins import registry
from opsmesh.plugins.channels import (
    AttachmentCollection,
    Message,
    MessageSegment,
    StreamMessageEnd,
    StreamMessageStart,
    StreamTextDelta,
    UnsupportedOperation,
    UserContext,
)
from opsmesh.plugins.channels.callbacks import EventQueue
from opsmesh.plugins.channels.dingtalk import (
    DingTalkAdapter,
    DingTalkConnectionSettings,
)
from opsmesh.plugins.channels.dingtalk.events import DingTalkMessageEvent
from opsmesh.plugins.channels.qq import QQAdapter, QQConnectionSettings
from opsmesh.plugins.channels.qq.events import QQMessageEvent
from opsmesh.plugins.channels.sending import prepare
from opsmesh.plugins.channels.streaming import message_stream
from opsmesh.plugins.manager import PluginManager
from opsmesh.plugins.model import Plugin, PluginStatus


class BotConnection:
    """One declared bot connection built by the real adapter contract."""

    def __init__(self, protocol):
        self.protocol = protocol
        self.driver = Driver(Env(), Config())
        self.owner = Plugin(
            "consumer",
            ModuleType("consumer"),
            "consumer",
            PluginManager(),
            status=PluginStatus.READY,
        )
        token = registry._current_plugin.set(self.owner)
        try:
            if protocol == "qq":
                self.adapter = QQAdapter(self.driver)
                self.channel = self.adapter.declare(
                    QQConnectionSettings(
                        app_id="123", secret_ref="BOT_SECRET"
                    ),
                    name="bot",
                )
            else:
                self.adapter = DingTalkAdapter(self.driver)
                self.channel = self.adapter.declare(
                    DingTalkConnectionSettings(
                        client_id="123", client_secret_ref="BOT_SECRET"
                    ),
                    name="bot",
                )
        finally:
            registry._current_plugin.reset(token)
        self.channel.mark_ready()

    def message_event(self):
        user = UserContext.scoped(self.channel.self_id, "user")
        if self.protocol == "qq":
            return QQMessageEvent(
                connection_id=self.channel.self_id,
                message_id="message",
                user=user,
                conversation_id="conversation",
                conversation_type="private",
                message=Message("hello"),
                to_me=True,
                user_id="user",
                event_id="event",
                raw={},
            )
        return DingTalkMessageEvent(
            connection_id=self.channel.self_id,
            message_id="message",
            user=user,
            conversation_id="conversation",
            conversation_type="private",
            message=Message("hello"),
            to_me=True,
            user_id="user",
            raw={},
        )


@pytest.mark.anyio
async def test_bot_output_omits_structured_data_without_turning_it_into_text():
    message = Message(
        [
            MessageSegment.text("Selected session"),
            MessageSegment.structured({"session_id": "session-123"}),
        ]
    )
    prepared = await prepare(
        message,
        AttachmentCollection(),
        2000,
        supported=frozenset({"text", "data"}),
    )
    assert prepared.parts == [
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
        await prepare(
            Message(segments),
            AttachmentCollection(),
            2000,
            supported=frozenset({"text", "data"}),
        )


@pytest.mark.parametrize(
    "value",
    [[], "text", object(), {"value": object()}, {"value": float("nan")}],
)
def test_structured_factory_rejects_non_json_objects(value):
    with pytest.raises((TypeError, ValueError)):
        MessageSegment.structured(value)


def test_structured_data_has_a_shared_size_limit(monkeypatch):
    from opsmesh.plugins.channels.messages import segments as messages

    monkeypatch.setattr(messages, "MAX_STRUCTURED_BYTES", 8)
    with pytest.raises(ValueError, match="size limit"):
        MessageSegment.structured({"large": "value"})


@pytest.mark.anyio
@pytest.mark.parametrize("protocol", ["qq", "dingtalk"])
async def test_bot_stream_without_native_support_rejects_and_closes_source(
    protocol,
):
    bot = BotConnection(protocol)
    stages = []

    async def source():
        try:
            yield StreamMessageStart("a")
            yield StreamTextDelta("a", 1, "one")
            assert stages == []
            yield StreamTextDelta("a", 2, " two")
            yield StreamMessageEnd("a")
        finally:
            stages.append("source closed")

    async def send(channel, event, message, **options):
        assert stages == []
        stages.append(str(message))

    bot.adapter._send_message = send
    with registry.plugin_scope(bot.owner):
        with pytest.raises(UnsupportedOperation):
            await bot.adapter._call(
                bot.channel,
                "stream",
                event=bot.message_event(),
                source=source(),
            )
    assert stages == ["source closed"]


@pytest.mark.anyio
async def test_stream_aggregation_closes_the_source_when_size_limit_is_exceeded(
    monkeypatch,
):
    from opsmesh.plugins.channels import streaming

    monkeypatch.setattr(streaming, "MAX_STREAM_BYTES", 5)
    closed = []

    async def source():
        try:
            yield StreamMessageStart("a")
            yield StreamTextDelta("a", 1, "first")
            yield StreamTextDelta("a", 2, "second")
            yield StreamMessageEnd("a")
        finally:
            closed.append(True)

    with pytest.raises(ValueError, match="size limit"):
        async with message_stream(source()) as reader:
            async for _, block in reader.blocks():
                await block.message()
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
        concurrency=2,
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
    assert not queue._active


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
        assert queue._started
        assert queue._worker_scopes
        assert not queue.put_nowait("new")
        with pytest.raises(RuntimeError, match="has not stopped"):
            await queue.start(tasks)
        release.set()
        await queue.stop()
    assert not queue._started
    assert not queue._worker_scopes
