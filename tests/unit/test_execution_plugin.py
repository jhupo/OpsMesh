"""Check the execution plugin's framework lifecycle integration."""

import asyncio
import logging

import pytest

from opsmesh.framework import runtime
from opsmesh.framework.config import Config, Env
from opsmesh.framework.drivers import Driver as BaseDriver
import opsmesh_execution as plugin
from opsmesh_execution import _lifecycle as lifecycle


class LifecycleDriver(BaseDriver):
    @property
    def type(self):
        return "test"

    @property
    def logger(self):
        return logging.getLogger(__name__)


@pytest.mark.anyio
async def test_lifecycle_starts_and_stops_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_driver", None)
    driver = LifecycleDriver(
        Env(),
        Config(
            plugin_configs={
                "opsmesh_execution": {},
            }
        ),
    )
    runtime.set_driver(driver)
    monkeypatch.setattr(lifecycle, "_runtime", None)
    driver.on_startup(lifecycle.startup)
    driver.on_shutdown(lifecycle.shutdown)

    async with driver.lifespan():
        execution = lifecycle.get_runtime()
        assert execution.started
        assert execution.calls.accepting
        assert "database" not in plugin.__plugin_meta__.extra

    with pytest.raises(plugin.ExecutionClosed):
        lifecycle.get_runtime()


def test_unstarted_execution_rejects_access():
    assert plugin.__plugin_meta__.name == "execution"
    with pytest.raises(plugin.ExecutionClosed):
        lifecycle.get_runtime()


@pytest.mark.anyio
async def test_execution_drains_before_consumers_release_resources(monkeypatch):
    monkeypatch.setattr(runtime, "_driver", None)
    monkeypatch.setattr(lifecycle, "_runtime", None)
    driver = LifecycleDriver(Env(), Config())
    runtime.set_driver(driver)
    driver.on_startup(lifecycle.startup)
    driver.on_stopping(lifecycle.stopping)
    driver.on_shutdown(lifecycle.shutdown)
    events = []
    entered = asyncio.Event()

    async def consumer_shutdown():
        events.append("consumer resources closed")

    driver.on_shutdown(consumer_shutdown)

    async def active_call():
        calls = lifecycle.get_runtime().calls
        call = calls.begin()
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append("execution finished")
            calls.end(call)

    async with driver.lifespan():
        task = asyncio.create_task(active_call())
        await entered.wait()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["execution finished", "consumer resources closed"]
