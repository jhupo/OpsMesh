import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from opsmesh.framework import load_plugin, require
from opsmesh.framework.adapters import OpsMeshAdapter, OpsMeshEvent
from opsmesh.framework.drivers import DriverState, HTTPRequest, OpsMeshDriver
from opsmesh.framework.drivers.httpx import HTTPXDriver
from opsmesh.framework.exception import PluginError
from opsmesh.framework.matcher import matchers
from opsmesh.framework.message import handle_event
from opsmesh.framework.plugin import get_current_plugin, manager
from opsmesh.framework.scheduling import Scheduler


@pytest.mark.asyncio
async def test_plugin_import_require_ownership_and_disable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "plug_shared.py").write_text("value = 42\n", encoding="utf-8")
    (tmp_path / "plug_user.py").write_text(
        """
from opsmesh.framework import require, on_event, OpsMeshPluginMetadata
assert __plugin__ is not None
shared = require("plug_shared")
__plugin_meta__ = OpsMeshPluginMetadata(name="user", description="test", usage="test")
seen = []
matcher = on_event("sample")
@matcher.handle()
async def receive():
    seen.append(shared.value)
""",
        encoding="utf-8",
    )
    plugin = load_plugin("plug_user")
    assert require("plug_user") is plugin.module
    assert plugin.module is not None
    assert plugin.module.matcher.owner is plugin
    assert get_current_plugin() is None
    await handle_event(OpsMeshEvent(type="sample"), raise_on_error=True)
    assert plugin.module.seen == [42]
    with pytest.raises(PluginError, match="depend"):
        manager.disable("plug_shared")
    manager.disable("plug_shared", cascade=True)
    assert not plugin.enabled
    assert not (await handle_event(OpsMeshEvent(type="sample"))).matched
    manager.enable("plug_shared")
    manager.enable("plug_user")
    assert (await handle_event(OpsMeshEvent(type="sample"))).matched == 1


def test_import_failure_rolls_back_known_registrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "plug_broken.py").write_text(
        """
from opsmesh.framework import on_event, get_driver
matcher = on_event("sample")
@get_driver().on_startup
async def startup():
    pass
raise ValueError("broken import")
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="broken import"):
        load_plugin("plug_broken")
    assert matchers.snapshot() == ()
    assert get_current_plugin() is None
    with pytest.raises(PluginError, match="restart"):
        load_plugin("plug_broken")


@pytest.mark.asyncio
async def test_driver_supervises_and_cleans_tasks_and_hooks() -> None:
    driver = OpsMeshDriver()
    entered = asyncio.Event()
    ended = asyncio.Event()
    calls: list[str] = []

    async def worker() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            ended.set()

    @driver.on_startup
    async def startup() -> None:
        calls.append("start")
        driver.create_task(worker())

    @driver.on_shutdown
    async def shutdown() -> None:
        assert ended.is_set()
        calls.append("stop")

    async with driver.lifespan():
        await asyncio.wait_for(entered.wait(), 2)
        assert driver.state == DriverState.RUNNING
    assert calls == ["start", "stop"]
    assert driver.state == DriverState.STOPPED


@pytest.mark.asyncio
async def test_start_failure_still_closes_created_resources() -> None:
    driver = OpsMeshDriver()
    calls: list[str] = []

    @driver.on_startup
    async def start() -> None:
        calls.append("open")
        raise ValueError("cannot start")

    @driver.on_shutdown
    async def stop() -> None:
        calls.append("close")

    with pytest.raises(ValueError, match="cannot start"):
        await driver.start()
    assert calls == ["open", "close"]
    assert driver.state == DriverState.STOPPED


@pytest.mark.asyncio
async def test_http_driver_executes_transport_and_rejects_closed_client() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.content == b"hello"
        return httpx.Response(201, content=b"created")

    driver = HTTPXDriver(transport=httpx.MockTransport(respond))
    async with driver.lifespan():
        response = await driver.request(HTTPRequest("POST", "https://local.test", content=b"hello"))
    assert response.status_code == 201 and response.content == b"created"
    with pytest.raises(RuntimeError, match="not running"):
        await driver.request(HTTPRequest("GET", "https://local.test"))


def test_adapter_network_capabilities_are_checked() -> None:
    class NetworkAdapter(OpsMeshAdapter):
        required_capabilities = frozenset({"http_client"})

    with pytest.raises(ValueError, match="http_client"):
        OpsMeshDriver().register_adapter(NetworkAdapter)


@pytest.mark.asyncio
async def test_scheduler_fires_without_bot_and_stops_with_driver() -> None:
    from opsmesh.framework import on_event
    from opsmesh.framework.events import TimerEvent

    driver = OpsMeshDriver()
    scheduler = Scheduler(driver)
    triggered = asyncio.Event()
    matcher = on_event(TimerEvent)

    @matcher.handle()
    async def receive(event: OpsMeshEvent) -> None:
        assert event.type == "timer.fired"
        triggered.set()

    scheduler.add_date_job("once", datetime.now(UTC))
    async with driver.lifespan():
        await asyncio.wait_for(triggered.wait(), 2)
    assert scheduler.errors == []
