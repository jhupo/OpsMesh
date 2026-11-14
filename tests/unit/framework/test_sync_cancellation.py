"""Keep worker threads inside their invocation and dependency lifetimes."""

import asyncio
import importlib
import threading
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace
from typing import Annotated
from unittest.mock import AsyncMock

import anyio
import pytest

from opsmesh.params import Depends
from opsmesh.utils import run_sync, run_sync_ctx_manager


class BlockingCall:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.started = asyncio.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.calls = 0
        self.error = None

    def __call__(self):
        self.calls += 1
        self.loop.call_soon_threadsafe(self.started.set)
        try:
            if not self.release.wait(5):
                raise RuntimeError("Test did not release its worker")
            if self.error is not None:
                raise self.error
            return 7
        finally:
            self.finished.set()


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["enter", "exit"])
async def test_sync_context_cancellation_finishes_acquisition_and_release(
    phase,
):
    worker = BlockingCall()
    callbacks = []

    class Resource:
        def __enter__(self):
            if phase == "enter":
                worker()
            callbacks.append("acquired")
            return self

        def __exit__(self, *error):
            if phase == "exit":
                worker()
            callbacks.append("released")

    async def invoke():
        async with run_sync_ctx_manager(Resource()):
            callbacks.append("body")

    task = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(worker.started.wait(), 2)
        task.cancel("initial cancellation")
        await asyncio.sleep(0)
        task.cancel("repeated cancellation")
        await asyncio.sleep(0)
        assert not task.done()
        worker.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        expected = ["acquired", "released"]
        if phase == "exit":
            expected.insert(1, "body")
        assert callbacks == expected
    finally:
        worker.release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_sync_context_failed_enter_does_not_exit():
    callbacks = []

    class Resource:
        def __enter__(self):
            raise ValueError("acquisition failed")

        def __exit__(self, *error):
            callbacks.append("released")

    with pytest.raises(ValueError, match="acquisition failed"):
        async with run_sync_ctx_manager(Resource()):
            pytest.fail("Failed acquisition reached the body")
    assert not callbacks


@pytest.mark.anyio
async def test_sync_context_can_suppress_body_error():
    class Resource:
        def __enter__(self):
            return self

        def __exit__(self, error_type, error, traceback):
            return error_type is ValueError

    async with run_sync_ctx_manager(Resource()):
        raise ValueError("suppressed")


@pytest.mark.anyio
async def test_sync_result_and_exception_are_preserved():
    assert await run_sync(lambda: 7)() == 7

    def fail():
        raise ValueError("worker failure")

    with pytest.raises(ValueError, match="worker failure"):
        await run_sync(fail)()


@pytest.mark.anyio
@pytest.mark.parametrize("worker_error", [None, RuntimeError("worker failed")])
async def test_native_repeated_cancellation_waits_for_the_same_thread(
    worker_error,
):
    worker = BlockingCall()
    worker.error = worker_error
    cleaned = asyncio.Event()

    async def invoke():
        try:
            await run_sync(worker)()
        finally:
            cleaned.set()

    task = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(worker.started.wait(), 2)
        task.cancel("initial cancellation")
        await asyncio.sleep(0)
        task.cancel("repeated cancellation")
        await asyncio.sleep(0)
        assert not task.done()
        assert not cleaned.is_set()
        worker.release.set()
        with pytest.raises(asyncio.CancelledError) as failure:
            await task
        assert failure.value.args == ("initial cancellation",)
        assert worker.finished.is_set()
        assert worker.calls == 1
        assert cleaned.is_set()
    finally:
        worker.release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("worker_error", [None, RuntimeError("worker failed")])
async def test_anyio_scope_cancellation_waits_for_thread_cleanup(worker_error):
    worker = BlockingCall()
    worker.error = worker_error
    cleaned = asyncio.Event()
    scope = None

    async def invoke():
        nonlocal scope
        with anyio.CancelScope() as scope:
            try:
                await run_sync(worker)()
            finally:
                cleaned.set()
        return scope.cancelled_caught

    task = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(worker.started.wait(), 2)
        scope.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()
        assert not cleaned.is_set()
        worker.release.set()
        assert await task
        assert worker.finished.is_set()
        assert worker.calls == 1
        assert cleaned.is_set()
    finally:
        worker.release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.fixture
def cron_runtime(monkeypatch):
    from opsmesh.config import Config, Env
    from opsmesh.drivers import Driver, base
    from opsmesh.matchers import matchers
    from opsmesh.plugins import registry
    from opsmesh.plugins.model import Plugin, PluginStatus

    saved_matchers = dict(matchers.items())
    monkeypatch.setattr(base, "_driver", Driver(Env(), Config()))
    database_module = importlib.import_module("opsmesh.plugins.database")
    database = Plugin(
        "database", database_module, database_module.__name__, None
    )
    monkeypatch.setattr(
        registry, "_plugins", {**registry._plugins, database.id_: database}
    )
    cron = importlib.import_module("opsmesh.plugins.cron")
    declaration_module = importlib.import_module(
        "opsmesh.plugins.cron.declarations"
    )
    scheduler = importlib.import_module("opsmesh.plugins.cron.scheduler")
    monkeypatch.setattr(declaration_module, "declarations", {})
    monkeypatch.setattr(
        scheduler, "declarations", declaration_module.declarations
    )
    owner = Plugin(
        "sync_consumer", ModuleType("sync_consumer"), "sync_consumer", None
    )
    owner.status = PluginStatus.READY
    monkeypatch.setattr(
        registry, "_plugins", {**registry._plugins, owner.id_: owner}
    )
    runtime = scheduler.Scheduler(SimpleNamespace(), cron.Settings())
    runtime._healthy = True
    runtime._check_lock = AsyncMock()
    runtime.channel = scheduler.ScheduleChannel(
        scheduler.ScheduleAdapter(base.get_driver()), "sync-cron"
    )
    definition = cron.ScheduleDefinition(
        name="sync",
        handler="sync",
        timeout_seconds=0.1,
        trigger=cron.IntervalDefinition(seconds=60, start_at=datetime.now(UTC)),
    )
    record = SimpleNamespace(
        id="schedule",
        owner_plugin=owner.id_,
        name="sync",
        revision="current",
        definition=definition.model_dump(mode="json"),
        completed=False,
        expired=False,
    )
    runtime.schedules = SimpleNamespace(
        get=AsyncMock(return_value=record), complete=AsyncMock()
    )
    try:
        with registry.plugin_scope(owner):
            yield cron, owner, runtime
    finally:
        matchers.clear()
        matchers.update(saved_matchers)


@pytest.mark.anyio
async def test_cron_timeout_keeps_plugin_and_dependencies_until_thread_exits(
    cron_runtime,
):
    cron, owner, runtime = cron_runtime
    worker = BlockingCall()
    cleaned = asyncio.Event()

    async def resource():
        try:
            yield object()
        finally:
            cleaned.set()

    matcher = cron.on_schedule("sync")

    @matcher.handle()
    def digest(value: Annotated[object, Depends(resource)]):
        return worker()

    firing = asyncio.create_task(
        runtime._fire(
            importlib.import_module(
                "opsmesh.plugins.cron.scheduler"
            )._TimerBinding("schedule", "current", owner, owner.generation)
        )
    )
    shutdown = None
    try:
        await asyncio.wait_for(worker.started.wait(), 2)
        await asyncio.sleep(0.15)
        assert not firing.done()
        assert owner.calls
        assert not cleaned.is_set()
        shutdown = asyncio.create_task(runtime.shutdown())
        await asyncio.sleep(0)
        assert not shutdown.done()
        worker.release.set()
        with pytest.raises(TimeoutError):
            await firing
        await shutdown
        assert cleaned.is_set()
        assert worker.finished.is_set()
        assert worker.calls == 1
        assert not owner.calls
        assert not runtime._active
    finally:
        worker.release.set()
        await asyncio.gather(firing, return_exceptions=True)
        if shutdown is not None:
            await shutdown
