"""Single-process interval/date scheduling supervised by a framework OpsMeshDriver.

Distributed deployments must elect one scheduling owner externally. Jobs never
overlap themselves; late intervals are skipped, and errors remain observable.
"""

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from opsmesh.framework.dependencies import DependencyScope
from opsmesh.framework.drivers import DriverState, OpsMeshDriver
from opsmesh.framework.events import DispatchContext, TimerEvent, bind_context
from opsmesh.framework.message import handle_event
from opsmesh.framework.plugin import OpsMeshPlugin, get_current_plugin


@dataclass(frozen=True)
class ScheduledJob:
    id: str
    callback: Callable[..., Any] | None = None
    interval: float | None = None
    at: datetime | None = None
    owner: OpsMeshPlugin | None = field(default=None, repr=False)


class Scheduler:
    def __init__(self, driver: OpsMeshDriver) -> None:
        self.driver = driver
        self.jobs: dict[str, ScheduledJob] = {}
        self.errors: list[tuple[str, Exception]] = []
        self._tasks: dict[str, asyncio.Task[None]] = {}
        driver.on_startup(self.start)
        driver.on_shutdown(self.stop)

    def add_interval_job(
        self,
        id: str,
        seconds: float,
        callback: Callable[..., Any] | None = None,
    ) -> ScheduledJob:
        if seconds <= 0 or not math.isfinite(seconds):
            raise ValueError("Interval must be positive and finite")
        return self._add(ScheduledJob(id, callback, interval=seconds))

    def add_date_job(
        self,
        id: str,
        at: datetime,
        callback: Callable[..., Any] | None = None,
    ) -> ScheduledJob:
        if at.tzinfo is None:
            raise ValueError("Scheduled time must be timezone-aware")
        return self._add(ScheduledJob(id, callback, at=at))

    def _add(self, job: ScheduledJob) -> ScheduledJob:
        job = replace(job, owner=get_current_plugin())
        if job.id in self.jobs:
            raise ValueError(f"Duplicate job: {job.id}")
        self.jobs[job.id] = job
        if self.driver.state == DriverState.RUNNING:
            self._spawn(job)
        return job

    def _spawn(self, job: ScheduledJob) -> None:
        self._tasks[job.id] = self.driver.create_task(self._run(job), name=f"scheduler:{job.id}")

    async def start(self) -> None:
        for job in self.jobs.values():
            self._spawn(job)

    async def remove_job(self, id: str) -> None:
        self.jobs.pop(id)
        task = self._tasks.pop(id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def stop(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run(self, job: ScheduledJob) -> None:
        loop = asyncio.get_running_loop()
        delay = (
            job.interval
            if job.interval is not None
            else max(0, ((job.at or datetime.now(UTC)) - datetime.now(UTC)).total_seconds())
        )
        deadline = loop.time() + delay
        while True:
            await asyncio.sleep(max(0, deadline - loop.time()))
            scheduled_at = datetime.now(UTC)
            event = TimerEvent(
                id=f"timer:{job.id}:{scheduled_at.isoformat()}",
                job_id=job.id,
                scheduled_at=scheduled_at,
                source="scheduler",
            )
            try:
                if job.owner is not None and not job.owner.enabled:
                    pass
                elif job.callback is None:
                    await handle_event(event, raise_on_error=True)
                else:
                    with bind_context(DispatchContext(event=event)):
                        async with DependencyScope({"event": event, "job": job}) as scope:
                            await scope.invoke(job.callback)
            except Exception as exc:
                self.errors.append((job.id, exc))
                del self.errors[:-100]
            if job.interval is None:
                return
            deadline += job.interval
            if deadline <= loop.time():
                deadline += (math.floor((loop.time() - deadline) / job.interval) + 1) * job.interval


__all__ = ["ScheduledJob", "Scheduler"]
