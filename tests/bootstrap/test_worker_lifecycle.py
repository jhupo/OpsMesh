"""A fatal native Worker must not leave a live, ineffective dispatch process."""

import asyncio
from types import SimpleNamespace

import pytest

import opsmesh.bootstrap.worker as workers
from tests.conftest import build_test_settings


def test_worker_failure_stops_dispatch_and_releases_resources(monkeypatch):
    async def verify():
        entered = asyncio.Event()
        released = []

        class Worker:
            async def run(self):
                await entered.wait()
                raise RuntimeError("fatal native Worker failure")

            async def shutdown(self):
                pytest.fail("A failed or never-started Worker must not be awaited for shutdown")

        class Dispatcher:
            async def run(self, stop):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    released.append("dispatcher")

        async def assemble(settings, resources):
            resources.callback(released.append, "resources")
            return SimpleNamespace(worker=Worker(), dispatcher=Dispatcher())

        monkeypatch.setattr(workers, "build_dispatch_worker", assemble)
        stop = asyncio.Event()
        with pytest.raises(RuntimeError, match="fatal native Worker failure"):
            await asyncio.wait_for(
                workers.run_dispatch_worker(
                    build_test_settings("postgresql+psycopg://test:test@unused/test"), stop
                ),
                2,
            )
        assert stop.is_set()
        assert released == ["dispatcher", "resources"]

    asyncio.run(verify())


def test_process_cancellation_stops_both_tasks_and_releases_resources(monkeypatch):
    async def verify():
        started = asyncio.Event()
        stopping = asyncio.Event()
        released = []

        class Worker:
            async def run(self):
                started.set()
                await stopping.wait()
                released.append("worker")

            async def shutdown(self):
                stopping.set()

        class Dispatcher:
            async def run(self, stop):
                try:
                    await asyncio.Event().wait()
                finally:
                    released.append("dispatcher")

        async def assemble(settings, resources):
            resources.callback(released.append, "resources")
            return SimpleNamespace(worker=Worker(), dispatcher=Dispatcher())

        monkeypatch.setattr(workers, "build_dispatch_worker", assemble)
        stop = asyncio.Event()
        task = asyncio.create_task(
            workers.run_dispatch_worker(
                build_test_settings("postgresql+psycopg://test:test@unused/test"), stop
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert stop.is_set()
        assert released == ["dispatcher", "worker", "resources"]

    asyncio.run(verify())


def test_normal_dispatch_stop_waits_for_native_worker_shutdown(monkeypatch):
    async def verify():
        started = asyncio.Event()
        stopping = asyncio.Event()
        finished = []

        class Worker:
            async def run(self):
                started.set()
                await stopping.wait()
                finished.append("worker")

            async def shutdown(self):
                finished.append("shutdown")
                stopping.set()

        class Dispatcher:
            async def run(self, stop):
                await started.wait()
                stop.set()
                finished.append("dispatcher")

        async def assemble(settings, resources):
            resources.callback(finished.append, "resources")
            return SimpleNamespace(worker=Worker(), dispatcher=Dispatcher())

        monkeypatch.setattr(workers, "build_dispatch_worker", assemble)
        await asyncio.wait_for(
            workers.run_dispatch_worker(
                build_test_settings("postgresql+psycopg://test:test@unused/test"), asyncio.Event()
            ),
            2,
        )
        assert finished == ["dispatcher", "shutdown", "worker", "resources"]

    asyncio.run(verify())
