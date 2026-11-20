"""Verify per-connection queue draining and reply lifetime."""

import time
from contextvars import copy_context
from types import ModuleType, SimpleNamespace

import anyio
import pytest

from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.exceptions import SkippedException
from opsmesh.plugins import registry
from opsmesh.plugins.channels import get_event_channel
from opsmesh.plugins.channels.callbacks import EventQueue
from opsmesh.plugins.channels.dingtalk import (
    DingTalkAdapter,
    DingTalkConnectionSettings,
)
from opsmesh.plugins.channels.dingtalk.events import parse_callback as dingtalk
from opsmesh.plugins.channels.qq import QQAdapter, QQConnectionSettings
from opsmesh.plugins.channels.qq.events import parse_callback as qq
from opsmesh.plugins.manager import PluginManager
from opsmesh.plugins.model import Plugin, PluginStatus


def make_channel(protocol, monkeypatch):
    driver = Driver(Env(), Config())
    owner = Plugin(
        "support",
        ModuleType("support"),
        "support",
        PluginManager(),
        status=PluginStatus.READY,
    )
    monkeypatch.setattr(registry, "_plugins", {"support": owner})
    monkeypatch.setattr(
        registry,
        "_module_plugins",
        {plugin.module_name: plugin for plugin in registry._plugins.values()},
    )
    token = registry._current_plugin.set(owner)
    try:
        if protocol == "qq":
            adapter = QQAdapter(driver)
            channel = adapter.declare(
                QQConnectionSettings(app_id="123", secret_ref="QQ_SECRET"),
                name="bot",
            )
        else:
            adapter = DingTalkAdapter(driver)
            channel = adapter.declare(
                DingTalkConnectionSettings(
                    client_id="123", client_secret_ref="DINGTALK_SECRET"
                ),
                name="bot",
            )
    finally:
        registry._current_plugin.reset(token)
    channel._prepared = True
    channel.mark_ready()
    return adapter, channel


def make_event(protocol, channel, message_id):
    if protocol == "qq":
        return qq(
            {
                "t": "C2C_MESSAGE_CREATE",
                "d": {
                    "id": message_id,
                    "content": message_id,
                    "author": {"user_openid": "user"},
                },
            },
            connection_id=channel.self_id,
        )
    return dingtalk(
        {
            "msgtype": "text",
            "text": {"content": message_id},
            "senderId": "user",
            "msgId": message_id,
            "conversationId": "conversation",
            "conversationType": "1",
        },
        connection_id=channel.self_id,
    )


async def wait_until(predicate):
    with anyio.fail_after(2):
        while not predicate():
            await anyio.sleep(0)


@pytest.mark.anyio
async def test_dispatch_binding_does_not_leak_to_other_events_or_tasks(
    monkeypatch,
):
    _, channel = make_channel("qq", monkeypatch)
    parent = make_event("qq", channel, "message")
    unrelated = parent.model_copy()
    inherited = None

    async def dispatch(event, **kwargs):
        nonlocal inherited
        assert get_event_channel(event) is channel
        if event is parent:
            inherited = copy_context()
            with pytest.raises(SkippedException):
                get_event_channel(unrelated)
            await channel.dispatch(unrelated)
            assert get_event_channel(parent) is channel
        else:
            with pytest.raises(SkippedException):
                get_event_channel(parent)

    monkeypatch.setattr(
        "opsmesh.plugins.channels.channel.dispatch_event", dispatch
    )
    await channel.dispatch(parent)
    with pytest.raises(SkippedException):
        get_event_channel(parent)
    with pytest.raises(SkippedException):
        inherited.run(get_event_channel, parent)
    await channel.close()


@pytest.mark.anyio
async def test_failed_cleanup_reserves_account_until_retry(monkeypatch):
    adapter, channel = make_channel("qq", monkeypatch)
    original_release = channel._release

    async def fail_release():
        raise RuntimeError("Outbound resource could not be closed")

    monkeypatch.setattr(channel, "_release", fail_release)
    with pytest.raises(ExceptionGroup, match="cleanup failed"):
        await channel.close()
    assert adapter.connections == (channel,)
    assert channel.dirty and not channel.ready

    owner = Plugin(
        "other",
        ModuleType("other"),
        "other",
        PluginManager(),
        status=PluginStatus.READY,
    )
    from opsmesh.plugins.channels.qq import QQConfigurationError

    with registry.plugin_scope(owner):
        with pytest.raises(QQConfigurationError, match="already declared"):
            adapter.declare(channel.settings)

    monkeypatch.setattr(channel, "_release", original_release)
    await channel.close()
    assert adapter.connections == ()
    assert not channel.replies_available


@pytest.mark.anyio
async def test_event_queue_cancels_stuck_workers_in_the_host_group():
    started, cancelled = anyio.Event(), anyio.Event()

    async def handle(item):
        started.set()
        try:
            await anyio.sleep_forever()
        finally:
            cancelled.set()

    queue = EventQueue(
        capacity=4,
        concurrency=1,
        shutdown_timeout=0.05,
        handle=handle,
    )
    async with anyio.create_task_group() as tasks:
        await queue.start(tasks)
        assert queue.put_nowait("item") is True
        await started.wait()
        began = time.monotonic()
        await queue.stop()
        assert time.monotonic() - began < 2
        assert queue.running is False
        assert cancelled.is_set()


@pytest.mark.anyio
@pytest.mark.parametrize("protocol", ["qq", "dingtalk"])
async def test_quiesce_drains_admitted_work_before_replies_close(
    monkeypatch, protocol
):
    adapter, channel = make_channel(protocol, monkeypatch)
    entered, release = anyio.Event(), anyio.Event()
    observed = []

    async def dispatch(event, **kwargs):
        observed.append(event.message_id)
        assert get_event_channel(event).replies_available
        entered.set()
        await release.wait()
        return SimpleNamespace(errors=[])

    monkeypatch.setattr(
        "opsmesh.plugins.channels.channel.dispatch_event", dispatch
    )
    event = make_event(protocol, channel, "message")
    quiesced = anyio.Event()

    async def wait_quiesce():
        await channel.quiesce()
        quiesced.set()

    async with anyio.create_task_group() as tasks:
        await channel._queue.start(tasks)
        assert await channel.accept(event, delivery_id="event") == "ok"
        await entered.wait()
        tasks.start_soon(wait_quiesce)
        await wait_until(lambda: not channel.ready)
        assert channel.replies_available
        assert not quiesced.is_set()
        release.set()
        await quiesced.wait()
    assert observed == ["message"]
    await channel.close()
    assert not channel.replies_available
    assert adapter.connections == ()


@pytest.mark.anyio
@pytest.mark.parametrize("protocol", ["qq", "dingtalk"])
async def test_disconnect_keeps_queued_work_until_replies_close(
    monkeypatch, protocol
):
    adapter, channel = make_channel(protocol, monkeypatch)
    entered, release = anyio.Event(), anyio.Event()
    observed = []

    async def dispatch(event, **kwargs):
        observed.append(event.message_id)
        if event.message_id == "first":
            entered.set()
            await release.wait()
        return SimpleNamespace(errors=[])

    monkeypatch.setattr(
        "opsmesh.plugins.channels.channel.dispatch_event", dispatch
    )
    first = make_event(protocol, channel, "first")
    second = make_event(protocol, channel, "second")
    async with anyio.create_task_group() as tasks:
        await channel._queue.start(tasks)
        assert await channel.accept(first, delivery_id="first") == "ok"
        assert await channel.accept(second, delivery_id="second") == "ok"
        await entered.wait()
        channel.inbound_task_disconnected()
        assert not channel.ready and channel.replies_available
        release.set()
        await channel.quiesce()
    assert observed == ["first", "second"]
    await channel.close()
    assert not channel.replies_available
    assert adapter.connections == ()
