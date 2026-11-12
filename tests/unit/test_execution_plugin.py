"""Check the execution plugin's framework lifecycle integration."""

import asyncio

import pytest

import opsmesh.plugins.execution as plugin
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.drivers import base as driver_state
from opsmesh.plugins.execution import _lifecycle as lifecycle


@pytest.fixture
def execution_settings(monkeypatch):
    """Supply settings for lifecycle tests without loading a plugin host."""
    monkeypatch.setattr(lifecycle, "get_plugin_config", lambda model: model())


@pytest.mark.anyio
async def test_lifecycle_starts_and_stops_execution(
    execution_settings, monkeypatch
):
    monkeypatch.setattr(driver_state, "_driver", None)
    driver = Driver(Env(), Config())
    driver_state.set_driver(driver)
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
async def test_execution_drains_before_consumers_release_resources(
    execution_settings, monkeypatch
):
    monkeypatch.setattr(driver_state, "_driver", None)
    monkeypatch.setattr(lifecycle, "_runtime", None)
    driver = Driver(Env(), Config())
    driver_state.set_driver(driver)
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
