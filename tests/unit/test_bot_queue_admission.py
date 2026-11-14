"""Keep acknowledged callbacks admitted through queue and dependency cleanup."""

import json
import time
from types import ModuleType
from typing import Annotated

import anyio
import pytest
from starlette.requests import Request

from opsmesh.adapters import Event
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.drivers import base as driver_state
from opsmesh.matchers import Matcher, MatcherSource, matchers
from opsmesh.params import Depends
from opsmesh.permissions import Permission
from opsmesh.plugins import registry
from opsmesh.plugins.channels.dingtalk import (
    DingTalkAdapter,
    DingTalkConnectionSettings,
    DingTalkSettings,
)
from opsmesh.plugins.channels.qq import (
    QQAdapter,
    QQConnectionSettings,
    QQSettings,
)
from opsmesh.plugins.channels.qq.security import QQSigner
from opsmesh.plugins.invocation import plugin_call
from opsmesh.plugins.model import Plugin, PluginStatus

SECRET = "0123456789abcdef0123456789abcdef"


class BotHarness:
    def __init__(self, protocol, monkeypatch):
        self.protocol = protocol
        self.driver = Driver(Env(), Config())
        monkeypatch.setattr(driver_state, "_driver", self.driver)
        self.provider = Plugin(
            "provider",
            ModuleType("provider"),
            "provider",
            None,
            status=PluginStatus.READY,
        )
        self.owner = Plugin(
            "support",
            ModuleType("support"),
            "support",
            None,
            dependencies=["provider"],
            status=PluginStatus.READY,
        )
        monkeypatch.setattr(
            registry,
            "_plugins",
            {"support": self.owner, "provider": self.provider},
        )
        token = registry._current_plugin.set(self.owner)
        try:
            if protocol == "qq":
                self.adapter = QQAdapter(
                    self.driver,
                    settings=QQSettings(
                        enabled=False,
                        concurrency=2,
                        max_pending_events=4,
                        shutdown_timeout=0.02,
                    ),
                )
                self.channel = self.adapter.declare(
                    QQConnectionSettings(app_id="123", secret_ref="BOT_SECRET"),
                    name="bot",
                )
                self.adapter._signers[self.channel.self_id] = QQSigner(SECRET)
            else:
                self.adapter = DingTalkAdapter(
                    self.driver,
                    settings=DingTalkSettings(
                        concurrency=2,
                        max_pending_events=4,
                        shutdown_timeout=0.02,
                    ),
                )
                self.channel = self.adapter.declare(
                    DingTalkConnectionSettings(
                        client_id="123",
                        client_secret_ref="BOT_SECRET",
                    ),
                    name="bot",
                )
                self.adapter._accepting = True
        finally:
            registry._current_plugin.reset(token)
        self.channel.mark_ready()

    async def admit(self, message_id):
        if self.protocol == "dingtalk":
            result, _ = await self.adapter.accept_callback(
                self.channel,
                {
                    "msgtype": "text",
                    "text": {"content": message_id},
                    "conversationId": "conversation",
                    "conversationType": "1",
                    "senderId": "user",
                    "msgId": message_id,
                    "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=test",
                    "sessionWebhookExpiredTime": int(time.time() * 1000)
                    + 3600000,
                },
            )
            return result
        body = json.dumps(
            {
                "op": 0,
                "t": "C2C_MESSAGE_CREATE",
                "id": message_id,
                "d": {
                    "id": message_id,
                    "content": message_id,
                    "author": {"user_openid": "user"},
                },
            }
        ).encode()
        stamp = str(int(time.time()))
        signature = QQSigner(SECRET).sign(stamp + body.decode())

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": self.channel.path,
                "headers": [
                    (b"x-signature-ed25519", signature.encode()),
                    (b"x-signature-timestamp", stamp.encode()),
                ],
            },
            receive,
        )
        response = await self.adapter._handle_callback(self.channel, request)
        return response.status_code

    def bind(self, handler, **options):
        return Matcher.new(
            type_="message",
            handlers=[handler],
            source=MatcherSource(plugin_id=self.owner.id_),
            **options,
        )

    def stop_owners(self):
        self.owner.status = PluginStatus.STOPPING
        self.provider.status = PluginStatus.STOPPING


@pytest.fixture(params=["qq", "dingtalk"])
def bot(request, monkeypatch):
    saved = dict(matchers.items())
    matchers.clear()
    harness = BotHarness(request.param, monkeypatch)
    try:
        yield harness
    finally:
        matchers.clear()
        matchers.update(saved)


@pytest.mark.anyio
async def test_acknowledged_queue_drains_stopping_plugins_and_finalizers(bot):
    stages = []

    async def resource(event: Event):
        stages.append((event.get_plaintext(), "open"))
        try:
            yield "value"
        finally:
            await anyio.sleep(0)
            async with plugin_call("provider"):
                stages.append((event.get_plaintext(), "close"))

    async def handle(event: Event, value: Annotated[str, Depends(resource)]):
        assert value == "value"
        stages.append((event.get_plaintext(), "handle"))

    bot.bind(handle)
    async with anyio.create_task_group() as tasks:
        await bot.adapter._queue.start(tasks)
        assert await bot.admit("one") == 200
        assert await bot.admit("two") == 200
        assert len(bot.owner.calls) == len(bot.provider.calls) == 2
        bot.stop_owners()
        bot.adapter.stop_accepting()
        await bot.adapter._queue.stop()
        expected = 503 if bot.protocol == "qq" else 500
        assert await bot.admit("late") == expected
    assert stages == [
        ("one", "open"),
        ("one", "handle"),
        ("one", "close"),
        ("two", "open"),
        ("two", "handle"),
        ("two", "close"),
    ]
    assert not bot.owner.calls and not bot.provider.calls


@pytest.mark.anyio
async def test_permissions_are_checked_when_dequeued_after_acknowledgement(bot):
    permissions, handled = [], []

    async def deny(event: Event):
        permissions.append(event.get_plaintext())
        return False

    async def handle():
        handled.append(True)

    bot.bind(handle, permission=Permission(deny))
    async with anyio.create_task_group() as tasks:
        await bot.adapter._queue.start(tasks)
        assert await bot.admit("one") == 200
        assert permissions == []
        bot.stop_owners()
        await bot.adapter._queue.stop()
    assert permissions == ["one"]
    assert handled == []
    assert not bot.owner.calls and not bot.provider.calls


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["full", "exception"])
async def test_failed_enqueue_releases_admission_and_allows_redelivery(
    bot,
    monkeypatch,
    failure,
):
    def reject(item):
        if failure == "exception":
            raise RuntimeError("enqueue failed")
        return False

    original = bot.adapter._queue.put_nowait
    monkeypatch.setattr(bot.adapter._queue, "put_nowait", reject)
    if failure == "exception":
        with pytest.raises(RuntimeError, match="enqueue failed"):
            await bot.admit("retry")
    else:
        assert await bot.admit("retry") in {500, 503}
    assert not bot.owner.calls and not bot.provider.calls
    monkeypatch.setattr(bot.adapter._queue, "put_nowait", original)
    async with anyio.create_task_group() as tasks:
        await bot.adapter._queue.start(tasks)
        assert await bot.admit("retry") == 200
        assert bot.owner.calls
        await bot.adapter._queue.stop()
    assert not bot.owner.calls and not bot.provider.calls


@pytest.mark.anyio
async def test_expired_activation_drops_event_and_releases_admission(
    bot, caplog
):
    handled = []

    async def handle():
        handled.append(True)

    bot.bind(handle)
    async with anyio.create_task_group() as tasks:
        await bot.adapter._queue.start(tasks)
        assert await bot.admit("old") == 200
        bot.owner.generation += 1
        with caplog.at_level("INFO"):
            await bot.adapter._queue.stop()
    assert handled == []
    assert "Cancelled queued" in caplog.text
    assert not bot.owner.calls and not bot.provider.calls


@pytest.mark.anyio
async def test_timeout_releases_active_locked_and_pending_items_once(bot):
    entered = anyio.Event()
    cleaned = []

    async def handle(event: Event):
        entered.set()
        await anyio.sleep_forever()

    async def release(item):
        cleaned.append(item.event.get_plaintext())
        await item.close()

    bot.bind(handle)
    bot.adapter._queue._release_item = release
    async with anyio.create_task_group() as tasks:
        await bot.adapter._queue.start(tasks)
        for number in range(4):
            assert await bot.admit(str(number)) == 200
        await entered.wait()
        bot.stop_owners()
        await bot.adapter._queue.stop()
        await bot.adapter._queue.stop()
    assert sorted(cleaned) == ["0", "1", "2", "3"]
    assert not bot.owner.calls and not bot.provider.calls
    assert not bot.adapter._queue._lanes


@pytest.mark.anyio
async def test_native_handler_failure_still_releases_dependencies(bot):
    finalized = []

    async def resource():
        try:
            yield "resource"
        finally:
            async with plugin_call("provider"):
                finalized.append(True)

    async def handle(value: Annotated[str, Depends(resource)]):
        raise RuntimeError("handler failed")

    bot.bind(handle)
    async with anyio.create_task_group() as tasks:
        await bot.adapter._queue.start(tasks)
        assert await bot.admit("fail") == 200
        bot.stop_owners()
        await bot.adapter._queue.stop()
    assert finalized == [True]
    assert not bot.owner.calls and not bot.provider.calls


@pytest.mark.anyio
async def test_delivery_keeps_its_own_admission_when_callback_inherits_one(bot):
    async with anyio.create_task_group() as tasks:
        await bot.adapter._queue.start(tasks)
        async with plugin_call("support") as original:
            assert await bot.admit("one") == 200
            calls = set(bot.owner.calls.values())
            assert original in calls and len(calls) == 2
        assert len(bot.owner.calls) == 1
        await bot.adapter._queue.stop()
    assert not bot.owner.calls and not bot.provider.calls


@pytest.mark.anyio
async def test_host_task_cancellation_releases_unconsumed_deliveries(bot):
    entered = anyio.Event()
    closed = []

    async def resource():
        try:
            yield "resource"
        except anyio.get_cancelled_exc_class():
            closed.append("cancelled")
            raise
        finally:
            await anyio.sleep(0)
            async with plugin_call("provider"):
                closed.append("closed")

    async def handle(value: Annotated[str, Depends(resource)]):
        entered.set()
        await anyio.sleep_forever()

    bot.bind(handle)
    async with anyio.create_task_group() as tasks:
        await bot.adapter._queue.start(tasks)
        for number in range(4):
            assert await bot.admit(str(number)) == 200
        await entered.wait()
        bot.stop_owners()
        tasks.cancel_scope.cancel()
    assert closed == ["cancelled", "closed"]
    assert not bot.owner.calls and not bot.provider.calls
    assert not bot.adapter._queue.running
    assert not bot.adapter._queue._lanes
