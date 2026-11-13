"""Check the execution plugin's framework lifecycle integration."""

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
        assert plugin.diagnostics()["destination"] == "disabled"
        assert "database" not in plugin.__plugin_meta__.extra

    with pytest.raises(plugin.ExecutionClosed):
        lifecycle.get_runtime()


def test_unstarted_execution_rejects_access():
    assert plugin.__plugin_meta__.name == "execution"
    with pytest.raises(plugin.ExecutionClosed):
        lifecycle.get_runtime()


@pytest.mark.anyio
async def test_failed_tracing_shutdown_retains_runtime_for_retry(
    execution_settings, monkeypatch
):
    monkeypatch.setattr(lifecycle, "_runtime", None)
    await lifecycle.startup()
    runtime = lifecycle.get_runtime()
    original = runtime.tracing.shutdown

    async def unavailable():
        raise RuntimeError("Exporter shutdown failed")

    monkeypatch.setattr(runtime.tracing, "shutdown", unavailable)
    with pytest.raises(RuntimeError, match="Exporter shutdown"):
        await lifecycle.shutdown()
    assert lifecycle.get_runtime() is runtime
    monkeypatch.setattr(runtime.tracing, "shutdown", original)
    await lifecycle.shutdown()
    assert lifecycle._runtime is None
