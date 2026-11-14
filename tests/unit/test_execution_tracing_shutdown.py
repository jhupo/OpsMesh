"""Verify retained Langfuse close tasks across interruption and retry."""

import asyncio
from threading import Event

import pytest

import opsmesh.plugins.execution._tracing.support as support_module
from opsmesh.plugins.execution._lifecycle import ExecutionRuntime
from opsmesh.plugins.execution._tracing.langfuse import LangfuseBridge
from opsmesh.plugins.execution.config import (
    LangfuseSettings,
    Settings,
    TracingSettings,
)
from opsmesh.plugins.execution.errors import TracingConfigurationError


class Instrumentor:
    def __init__(self):
        self.calls = 0
        self.error = None

    def uninstrument(self):
        self.calls += 1
        if self.error is not None:
            raise self.error


class Provider:
    def __init__(self):
        self.started = Event()
        self.release = Event()
        self.closed = Event()
        self.flush_calls = 0
        self.shutdown_calls = 0
        self.flush_error = None
        self.shutdown_error = None

    def force_flush(self, timeout_millis):
        self.flush_calls += 1
        if self.flush_error is not None:
            raise self.flush_error
        return False

    def shutdown(self):
        self.shutdown_calls += 1
        self.started.set()
        if not self.release.wait(timeout=2):
            raise RuntimeError("The test did not release its worker")
        if self.shutdown_error is not None:
            raise self.shutdown_error
        self.closed.set()


@pytest.fixture
def bridge():
    instance = LangfuseBridge(
        LangfuseSettings(
            base_url="https://langfuse.invalid",
            public_key_ref="PUBLIC",
            secret_key_ref="SECRET",
            shutdown_timeout_seconds=0.02,
        )
    )
    instance._instrumentor = Instrumentor()
    instance._provider = Provider()
    yield instance
    if instance._provider is not None:
        instance._provider.release.set()


@pytest.mark.anyio
async def test_timeout_retry_awaits_one_provider_close_task(bridge):
    provider, instrumentor = bridge._provider, bridge._instrumentor
    try:
        with pytest.raises(TimeoutError):
            await bridge.shutdown()
        close_task = bridge._shutdown_task
        assert await asyncio.to_thread(provider.started.wait, 1)
        assert not close_task.done()
        assert bridge._provider is provider
        assert instrumentor.calls == 1
        assert bridge._instrumentor is None
        with pytest.raises(TracingConfigurationError, match="has not stopped"):
            bridge.install()
        with pytest.raises(TimeoutError):
            await bridge.shutdown()
        assert bridge._shutdown_task is close_task
        assert provider.shutdown_calls == 1
    finally:
        provider.release.set()
        await bridge.shutdown()
    assert provider.closed.is_set()
    assert provider.flush_calls == 1
    assert bridge._provider is None
    assert bridge._shutdown_task is None
    await bridge.shutdown()
    assert provider.shutdown_calls == 1


@pytest.mark.anyio
async def test_cancelled_shutdown_keeps_the_running_close_task_for_retry(
    bridge,
):
    provider = bridge._provider
    # Cancellation must arrive while shutdown waits for the provider thread.
    bridge._settings = bridge._settings.model_copy(
        update={
            "shutdown_timeout_seconds": 1,
        }
    )
    waiter = asyncio.create_task(bridge.shutdown())
    try:
        assert await asyncio.to_thread(provider.started.wait, 1)
        close_task = bridge._shutdown_task
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert bridge._provider is provider
        assert not close_task.cancelled()
        assert bridge.diagnostics()["shutdown_pending"]
    finally:
        provider.release.set()
        await bridge.shutdown()
    assert provider.shutdown_calls == 1
    assert provider.closed.is_set()


@pytest.mark.anyio
async def test_concurrent_shutdown_callers_share_the_same_close_task(bridge):
    provider = bridge._provider
    bridge._settings = bridge._settings.model_copy(
        update={
            "shutdown_timeout_seconds": 1,
        }
    )
    first = asyncio.create_task(bridge.shutdown())
    second = asyncio.create_task(bridge.shutdown())
    try:
        assert await asyncio.to_thread(provider.started.wait, 1)
        assert provider.shutdown_calls == 1
    finally:
        provider.release.set()
        await asyncio.gather(first, second)
    assert provider.shutdown_calls == 1
    assert bridge._provider is None


@pytest.mark.anyio
async def test_uninstrument_failure_retains_both_resources_and_allows_retry(
    bridge,
):
    provider, instrumentor = bridge._provider, bridge._instrumentor
    instrumentor.error = RuntimeError("uninstrument failed")
    with pytest.raises(RuntimeError, match="uninstrument failed"):
        await bridge.shutdown()
    assert bridge._instrumentor is instrumentor
    assert bridge._provider is provider
    assert bridge._shutdown_task is None
    assert provider.shutdown_calls == 0
    instrumentor.error = None
    provider.release.set()
    await bridge.shutdown()
    assert instrumentor.calls == 2
    assert provider.shutdown_calls == 1
    assert bridge._instrumentor is None
    assert bridge._provider is None


@pytest.mark.anyio
async def test_provider_close_failure_stays_visible_without_duplicate_shutdown(
    bridge,
):
    provider = bridge._provider
    provider.shutdown_error = RuntimeError("private provider credential")
    provider.release.set()
    with pytest.raises(RuntimeError, match="private provider credential"):
        await bridge.shutdown()
    close_task = bridge._shutdown_task
    assert bridge._provider is provider
    assert bridge.diagnostics()["shutdown_failed"]
    assert "private" not in str(bridge.diagnostics())
    with pytest.raises(RuntimeError, match="private provider credential"):
        await bridge.shutdown()
    assert bridge._shutdown_task is close_task
    assert provider.shutdown_calls == 1


@pytest.mark.anyio
async def test_flush_failure_does_not_skip_provider_release(bridge, caplog):
    provider = bridge._provider
    provider.flush_error = RuntimeError("private flush credential")
    provider.release.set()
    await bridge.shutdown()
    assert provider.closed.is_set()
    assert provider.shutdown_calls == 1
    assert bridge._provider is None
    assert "private" not in caplog.text


@pytest.mark.anyio
async def test_runtime_keeps_installed_state_until_provider_close_is_confirmed(
    bridge,
    monkeypatch,
):
    processors, disabled = [], []
    monkeypatch.setattr(
        support_module, "set_trace_processors", processors.append
    )
    monkeypatch.setattr(support_module, "set_tracing_disabled", disabled.append)
    runtime = ExecutionRuntime(
        Settings(
            tracing=TracingSettings(
                destination="langfuse",
                langfuse=bridge._settings,
            )
        )
    )
    runtime.started = True
    runtime.tracing._installed = True
    runtime.tracing._bridge = bridge
    provider = bridge._provider
    try:
        with pytest.raises(TimeoutError):
            await runtime.shutdown()
        assert runtime.started
        assert runtime.tracing.installed
        assert runtime.tracing._bridge is bridge
        assert runtime.tracing.diagnostics()["langfuse"]["shutdown_pending"]
        assert not processors and not disabled
    finally:
        provider.release.set()
        await runtime.shutdown()
    assert not runtime.started
    assert not runtime.tracing.installed
    assert runtime.tracing._bridge is None
    assert processors == [[]] and disabled == [True]
