"""Verify channel background work shares the driver lifespan."""

import time
from contextlib import contextmanager

import anyio
import pytest

from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.plugins import registry
from opsmesh.plugins.channels._queue import EventQueue
from opsmesh.plugins.channels.dingtalk import (
    DingTalkAdapter,
    DingTalkConfigurationError,
    DingTalkConnectionSettings,
    DingTalkSettings,
)
from opsmesh.plugins.channels.qq import (
    QQAdapter,
    QQConfigurationError,
    QQConnectionSettings,
    QQSettings,
)
from opsmesh.plugins.model import PluginStatus

QQ_SECRET = "0123456789abcdef0123456789abcdef"
DINGTALK_SECRET = "dingtalk-test-secret"


class FakePlugin:
    """A minimal loaded plugin for declaration ownership."""

    def __init__(self, id_: str) -> None:
        self.ready = True
        self.id_ = id_
        self.cleanups = []
        self.status = PluginStatus.LOADED


@contextmanager
def owned_by(plugin):
    token = registry._current_plugin.set(plugin)
    try:
        yield plugin
    finally:
        registry._current_plugin.reset(token)


@pytest.fixture(autouse=True)
def connected_stream(monkeypatch):
    from opsmesh.plugins.channels.dingtalk.stream import DingTalkStream

    async def receive(stream):
        stream.adapter.stream_connected(stream.channel)
        await anyio.sleep_forever()

    monkeypatch.setattr(DingTalkStream, "_receive", receive)


async def wait_ready(channel):
    with anyio.fail_after(2):
        while not channel.ready:
            await anyio.sleep(0)


def make_runtime() -> Driver:
    driver = Driver(Env(), Config())
    return driver


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
    driver = make_runtime()
    qq = QQAdapter(driver, settings=QQSettings(enabled=False))
    dingtalk = DingTalkAdapter(driver, settings=DingTalkSettings())
    qq_channel = declare_qq(qq)
    dingtalk_channel = declare_dingtalk(dingtalk)
    driver.on_startup(qq.startup)
    driver.on_startup(dingtalk.startup)
    driver.on_started(dingtalk.start_accepting)
    driver.on_shutdown(dingtalk.shutdown)
    driver.on_shutdown(qq.shutdown)

    async with driver.lifespan():
        await wait_ready(dingtalk_channel)
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
    qq = QQAdapter(
        make_runtime(),
        settings=QQSettings(),
    )
    declare_qq(qq)
    with pytest.raises(QQConfigurationError, match="running runtime lifespan"):
        await qq.startup()

    dingtalk = DingTalkAdapter(
        make_runtime(),
        settings=DingTalkSettings(),
    )
    declare_dingtalk(dingtalk)
    with pytest.raises(
        DingTalkConfigurationError, match="running runtime lifespan"
    ):
        await dingtalk.startup()


@pytest.mark.anyio
async def test_qq_startup_failure_rolls_back_routes_and_workers(monkeypatch):
    monkeypatch.setenv("QQ_SECRET", QQ_SECRET)
    driver = make_runtime()
    adapter = QQAdapter(driver, settings=QQSettings(enabled=False))
    first = declare_qq(adapter, "first")
    second = declare_qq(adapter, "second")

    async def handler(request):
        return None

    adapter.app.add_api_route(second.path, handler, methods=["POST"])
    occupied = adapter.app.router.routes[-1]

    async with driver.lifespan():
        with pytest.raises(QQConfigurationError, match="unavailable"):
            await adapter.startup()
        assert adapter.started is False
        assert driver.channels == {}
        assert first.state == "closed"
        assert second.state == "closed"

        adapter.app.router.routes.remove(occupied)
        await adapter.startup()
        assert adapter.started is True
        assert first.ready is True
        assert second.ready is True
        await adapter.shutdown()


@pytest.mark.anyio
async def test_channels_drain_before_consumers_and_transports_close(
    monkeypatch,
):
    from opsmesh.plugins.channels.lifecycle import (
        transports as transport_module,
    )

    monkeypatch.setenv("QQ_SECRET", QQ_SECRET)
    monkeypatch.setenv("DINGTALK_SECRET", DINGTALK_SECRET)
    driver = make_runtime()
    qq = QQAdapter(driver, settings=QQSettings(enabled=False))
    dingtalk = DingTalkAdapter(driver, settings=DingTalkSettings())
    qq_channel = declare_qq(qq)
    dingtalk_channel = declare_dingtalk(dingtalk)
    transports = transport_module.ChannelTransports()
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
    driver.on_started(transports.start_accepting)
    driver.on_stopping(transports.stopping)
    driver.on_shutdown(transports.shutdown)
    driver.on_shutdown(close_consumer)
    async with driver.lifespan():
        await wait_ready(dingtalk_channel)
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
    from opsmesh.plugins.channels.lifecycle.transports import ChannelTransports

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

    lifecycle = ChannelTransports()
    await lifecycle.startup(DynamicTransport())
    try:
        await lifecycle.stopping()
    finally:
        await lifecycle.shutdown()
    assert events == ["start", "stop", "close"]


@pytest.mark.anyio
async def test_partial_startup_closes_every_acquired_transport():
    from opsmesh.plugins.channels.lifecycle.transports import ChannelTransports

    events = []
    first = ProbeTransport("first", events)
    failing = ProbeTransport("failing", events)
    unused = ProbeTransport("unused", events)
    failing.start_error = ValueError("Invalid protocol configuration")
    failing.close_error = RuntimeError("Injected cleanup failure")
    lifecycle = ChannelTransports()
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
    assert lifecycle._running
    assert lifecycle._started == [failing]
    failing.close_error = None
    await lifecycle.shutdown()
    assert events[-1] == ("failing", "close")
    assert not lifecycle._started


@pytest.mark.anyio
async def test_revoking_one_consumer_keeps_other_transport_claims():
    from opsmesh.plugins.channels.lifecycle.transports import ChannelTransports

    events = []
    transport = ProbeTransport("shared", events)
    lifecycle = ChannelTransports()
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
    from opsmesh.plugins.channels.lifecycle.transports import ChannelTransports

    events = []
    acquired = anyio.Event()
    transport = ProbeTransport("partial", events)

    async def startup():
        acquired.set()
        await anyio.sleep_forever()

    transport.startup = startup
    lifecycle = ChannelTransports()
    lifecycle.use(transport)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(lifecycle.startup)
        await acquired.wait()
        tasks.cancel_scope.cancel()
    assert events == [("partial", "close")]
    assert not lifecycle._started


@pytest.mark.anyio
async def test_shared_transport_restarts_after_last_consumer_stops(monkeypatch):
    from types import ModuleType

    from opsmesh.drivers import base as driver_state
    from opsmesh.plugins.channels.lifecycle.transports import ChannelTransports
    from opsmesh.plugins.model import Plugin

    driver = make_runtime()
    monkeypatch.setattr(driver_state, "_driver", driver)
    first = Plugin("first", ModuleType("first"), "first", None)
    second = Plugin("second", ModuleType("second"), "second", None)
    monkeypatch.setattr(
        registry, "_plugins", {"first": first, "second": second}
    )
    events = []
    transport = ProbeTransport("shared", events)
    resources = ChannelTransports()
    with owned_by(first):
        resources.use(transport)
    with owned_by(second):
        resources.use(transport)
    async with driver.lifespan():
        await driver._lifespan.stop_plugin(first)
        assert events == [("shared", "start")]
        await driver._lifespan.stop_plugin(second)
        assert events[-1] == ("shared", "close")
        await driver._lifespan.start_plugin(first)
        assert events[-1] == ("shared", "start")
    assert events == [
        ("shared", "start"),
        ("shared", "close"),
        ("shared", "start"),
        ("shared", "close"),
    ]


@pytest.mark.anyio
async def test_http_listener_retains_resources_when_cleanup_fails(monkeypatch):
    from types import SimpleNamespace

    from opsmesh.plugins.channels.server import ListenerSettings, Server

    async def app(scope, receive, send):
        return None

    server = Server(app, ListenerSettings())
    closed = []
    listener = SimpleNamespace()
    sock = SimpleNamespace(close=lambda: closed.append(True))
    server._server, server._socket = listener, sock

    async def fail(*args):
        raise RuntimeError("Injected listener cleanup failure")

    monkeypatch.setattr(server, "_drain_server", fail)
    with pytest.raises(RuntimeError, match="cleanup"):
        await server._stop_listener()
    assert (server._server, server._socket, closed) == (listener, sock, [])
    with pytest.raises(RuntimeError, match="not clean"):
        await server.startup(None)

    async def finish(*args):
        return None

    monkeypatch.setattr(server, "_drain_server", finish)
    await server._stop_listener()
    assert (server._server, server._socket, closed) == (None, None, [True])


@pytest.mark.anyio
async def test_failed_shared_transport_cannot_accept_a_new_consumer(
    monkeypatch,
):
    from types import ModuleType

    import opsmesh.drivers
    from opsmesh.plugins.channels.lifecycle.transports import ChannelTransports
    from opsmesh.plugins.model import Plugin

    class Hooks:
        def __init__(self):
            self.ready, self.started, self.shutdown = [], [], []

        def on_ready(self, function):
            self.ready.append(function)

        def on_started(self, function):
            self.started.append(function)

        def on_shutdown(self, function):
            self.shutdown.append(function)

    class SharedTransport(ProbeTransport):
        async def start_accepting(self):
            self.events.append((self.name, "accept"))

    hooks = Hooks()
    monkeypatch.setattr(opsmesh.drivers, "get_driver", lambda: hooks)
    resources = ChannelTransports()
    events = []
    transport = SharedTransport("shared", events)
    first = Plugin("first", ModuleType("first"), "first", None)
    second = Plugin("second", ModuleType("second"), "second", None)
    with owned_by(first):
        resources.use(transport)
    await hooks.ready[0]()
    await hooks.started[0]()
    transport.close_error = RuntimeError("Injected cleanup failure")
    with pytest.raises(RuntimeError, match="cleanup"):
        await hooks.shutdown[0]()
    with owned_by(second):
        resources.use(transport)
    for operation in (
        hooks.ready[1],
        hooks.started[1],
        resources.startup,
        resources.start_accepting,
    ):
        with pytest.raises(RuntimeError, match="successful cleanup"):
            await operation()
    assert events == [
        ("shared", "start"),
        ("shared", "accept"),
        ("shared", "close"),
    ]
    transport.close_error = None
    await hooks.shutdown[0]()
    await hooks.ready[1]()
    await hooks.started[1]()
    assert events[-3:] == [
        ("shared", "close"),
        ("shared", "start"),
        ("shared", "accept"),
    ]
    await hooks.shutdown[1]()
