"""Verify channel background work shares the driver lifespan."""

import time
from contextlib import contextmanager

import anyio
import pytest

from opsmesh.drivers.http import HTTPDriver
from opsmesh.framework.config import Config, Env
from opsmesh.framework.drivers import URL, HTTPServerSetup
from opsmesh.framework.plugins import registry
from opsmesh_channels._queue import EventQueue
from opsmesh_channels.dingtalk import (
    DingTalkAdapter,
    DingTalkConfigurationError,
    DingTalkConnectionSettings,
    DingTalkSettings,
)
from opsmesh_channels.qq import (
    QQAdapter,
    QQConfigurationError,
    QQConnectionSettings,
    QQSettings,
)

QQ_SECRET = "0123456789abcdef0123456789abcdef"
DINGTALK_SECRET = "dingtalk-test-secret"


class FakePlugin:
    """A minimal loaded plugin for declaration ownership."""

    def __init__(self, id_: str) -> None:
        self.ready = True
        self.id_ = id_
        self.cleanups = []


@contextmanager
def owned_by(plugin):
    token = registry._current_plugin.set(plugin)
    try:
        yield plugin
    finally:
        registry._current_plugin.reset(token)


def make_driver() -> HTTPDriver:
    return HTTPDriver(
        Env(), Config(driver_options={"http": {"enabled": False}})
    )


def declare_qq(adapter, name="primary"):
    settings = QQConnectionSettings(app_id="12345", secret_ref="QQ_SECRET")
    with owned_by(FakePlugin("support")):
        return adapter.declare(settings, name=name)


def declare_dingtalk(adapter, name="internal"):
    settings = DingTalkConnectionSettings(
        client_id="client-1", client_secret_ref="DINGTALK_SECRET"
    )
    with owned_by(FakePlugin("support")):
        return adapter.declare(settings, name=name)


@pytest.mark.anyio
async def test_two_channel_adapters_share_one_driver_lifespan(monkeypatch):
    monkeypatch.setenv("QQ_SECRET", QQ_SECRET)
    monkeypatch.setenv("DINGTALK_SECRET", DINGTALK_SECRET)
    driver = make_driver()
    qq = QQAdapter(driver, settings=QQSettings())
    dingtalk = DingTalkAdapter(driver, settings=DingTalkSettings())
    qq_channel = declare_qq(qq)
    dingtalk_channel = declare_dingtalk(dingtalk)
    driver.on_startup(qq.startup)
    driver.on_startup(dingtalk.startup)
    driver.on_shutdown(dingtalk.shutdown)
    driver.on_shutdown(qq.shutdown)

    async with driver.lifespan():
        assert qq_channel.ready is True
        assert dingtalk_channel.ready is True

    assert qq_channel.ready is False
    assert dingtalk_channel.ready is False
    assert driver.channels == {}
    assert driver.started is False


@pytest.mark.anyio
async def test_event_queue_cancels_stuck_workers_in_the_host_group():
    started = anyio.Event()
    cancelled = anyio.Event()

    async def handle(item):
        started.set()
        try:
            await anyio.sleep_forever()
        finally:
            cancelled.set()

    queue = EventQueue(
        capacity=4,
        concurrency=1,
        shutdown_timeout=0.2,
        handle=handle,
    )

    async with anyio.create_task_group() as task_group:
        await queue.start(task_group)
        assert queue.put_nowait("item") is True
        with anyio.fail_after(2):
            await started.wait()
        began = time.monotonic()
        await queue.stop()
        assert time.monotonic() - began < 2
        assert queue.running is False
        assert cancelled.is_set()


@pytest.mark.anyio
async def test_channel_startup_requires_a_running_driver_lifespan():
    qq = QQAdapter(make_driver(), settings=QQSettings())
    declare_qq(qq)
    with pytest.raises(QQConfigurationError, match="running driver lifespan"):
        await qq.startup()

    dingtalk = DingTalkAdapter(make_driver(), settings=DingTalkSettings())
    declare_dingtalk(dingtalk)
    with pytest.raises(
        DingTalkConfigurationError, match="running driver lifespan"
    ):
        await dingtalk.startup()


@pytest.mark.anyio
async def test_qq_startup_failure_rolls_back_routes_and_workers(monkeypatch):
    monkeypatch.setenv("QQ_SECRET", QQ_SECRET)
    driver = make_driver()
    adapter = QQAdapter(driver, settings=QQSettings())
    first = declare_qq(adapter, "first")
    second = declare_qq(adapter, "second")

    async def handler(request):
        return None

    driver.setup_http_server(
        HTTPServerSetup(URL(second.path), "POST", "occupied", handler)
    )

    async with driver.lifespan():
        with pytest.raises(QQConfigurationError, match="unavailable"):
            await adapter.startup()
        assert adapter.started is False
        assert driver.channels == {}
        assert first.state == "closed"
        assert second.state == "closed"

        driver.remove_http_server("POST", second.path)
        await adapter.startup()
        assert adapter.started is True
        assert first.ready is True
        assert second.ready is True
        await adapter.shutdown()


@pytest.mark.anyio
async def test_channels_drain_before_consumers_and_transports_close(
    monkeypatch,
):
    from opsmesh_channels import lifecycle

    monkeypatch.setenv("QQ_SECRET", QQ_SECRET)
    monkeypatch.setenv("DINGTALK_SECRET", DINGTALK_SECRET)
    driver = make_driver()
    qq = QQAdapter(driver, settings=QQSettings())
    dingtalk = DingTalkAdapter(driver, settings=DingTalkSettings())
    qq_channel = declare_qq(qq)
    dingtalk_channel = declare_dingtalk(dingtalk)
    transports = lifecycle.ChannelLifecycles()
    transports.use(qq)
    transports.use(dingtalk)
    consumer = FakePlugin("consumer")
    handled = []
    release = anyio.Event()

    async def handle(item):
        await release.wait()
        assert consumer.ready
        assert qq_channel.ready and dingtalk_channel.ready
        assert qq.started and dingtalk.started
        assert not qq._queue.running and not dingtalk._queue.running
        assert not qq._queue.put_nowait("late")
        assert not dingtalk._queue.put_nowait("late")
        handled.append(item)

    qq._queue._handle = handle
    dingtalk._queue._handle = handle

    async def drain_dingtalk():
        release.set()
        await original_drain()

    original_drain = dingtalk.drain
    monkeypatch.setattr(dingtalk, "drain", drain_dingtalk)

    async def close_consumer():
        assert sorted(handled) == ["dingtalk", "qq"]
        assert qq_channel.ready and dingtalk_channel.ready
        consumer.ready = False

    driver.on_startup(transports.startup)
    driver.on_stopping(transports.stopping)
    driver.on_shutdown(transports.shutdown)
    driver.on_shutdown(close_consumer)
    async with driver.lifespan():
        assert qq._queue.put_nowait("qq")
        assert dingtalk._queue.put_nowait("dingtalk")
    assert not consumer.ready
    assert not qq_channel.ready and not dingtalk_channel.ready
    assert driver.channels == {}


class ProbeTransport:
    """Observe cross-transport ordering and inject lifecycle failures."""

    def __init__(self, name, events):
        self.name = name
        self.events = events
        self.start_error = None
        self.close_error = None

    async def startup(self):
        self.events.append((self.name, "start"))
        if self.start_error:
            raise self.start_error

    def stop_accepting(self):
        self.events.append((self.name, "stop"))

    async def drain(self):
        self.events.append((self.name, "drain"))

    async def shutdown(self):
        self.events.append((self.name, "close"))
        if self.close_error:
            raise self.close_error


@pytest.mark.anyio
async def test_dynamic_operations_are_not_used_as_lifecycle_hooks():
    from opsmesh_channels.lifecycle import ChannelLifecycles

    events = []

    class DynamicTransport:
        async def startup(self):
            events.append("start")

        def stop_accepting(self):
            events.append("stop")

        async def shutdown(self):
            events.append("close")

        def __getattr__(self, operation):
            async def call():
                pytest.fail(f"Lifecycle invoked dynamic operation: {operation}")

            return call

    lifecycle = ChannelLifecycles()
    await lifecycle.startup(DynamicTransport())
    try:
        await lifecycle.stopping()
    finally:
        await lifecycle.shutdown()
    assert events == ["start", "stop", "close"]


@pytest.mark.anyio
async def test_partial_startup_closes_every_acquired_transport():
    from opsmesh_channels.lifecycle import ChannelLifecycles

    events = []
    first = ProbeTransport("first", events)
    failing = ProbeTransport("failing", events)
    unused = ProbeTransport("unused", events)
    failing.start_error = ValueError("Invalid protocol configuration")
    failing.close_error = RuntimeError("Injected cleanup failure")
    lifecycle = ChannelLifecycles()
    for transport in (first, failing, unused):
        lifecycle.use(transport)
    with pytest.raises(ExceptionGroup) as failure:
        await lifecycle.startup()
    assert isinstance(failure.value.exceptions[0], ValueError)
    assert events == [
        ("first", "start"),
        ("failing", "start"),
        ("failing", "close"),
        ("first", "close"),
    ]
    assert not lifecycle._running
    assert not lifecycle._started


@pytest.mark.anyio
async def test_revoking_one_consumer_keeps_other_transport_claims():
    from opsmesh_channels.lifecycle import ChannelLifecycles

    events = []
    transport = ProbeTransport("shared", events)
    lifecycle = ChannelLifecycles()
    first = FakePlugin("first")
    second = FakePlugin("second")
    with owned_by(first):
        lifecycle.use(transport)
    with owned_by(second):
        lifecycle.use(transport)
    for cleanup in first.cleanups:
        cleanup()
    await lifecycle.startup()
    await lifecycle.shutdown()
    assert events == [("shared", "start"), ("shared", "close")]
    for cleanup in second.cleanups:
        cleanup()
    events.clear()
    await lifecycle.startup()
    await lifecycle.shutdown()
    assert not events


@pytest.mark.anyio
async def test_cancelled_startup_still_releases_partial_resources():
    from opsmesh_channels.lifecycle import ChannelLifecycles

    events = []
    acquired = anyio.Event()
    transport = ProbeTransport("partial", events)

    async def startup():
        acquired.set()
        await anyio.sleep_forever()

    transport.startup = startup
    lifecycle = ChannelLifecycles()
    lifecycle.use(transport)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(lifecycle.startup)
        await acquired.wait()
        tasks.cancel_scope.cancel()
    assert events == [("partial", "close")]
    assert not lifecycle._started
