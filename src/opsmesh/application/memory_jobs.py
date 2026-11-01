"""Supervise memory jobs without blocking Run scheduling or event delivery."""

import asyncio
from time import perf_counter
from typing import Any

from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.infrastructure.telemetry.metrics import PlatformMetrics
from opsmesh.modules.memory.api import MemoryService


class MemoryJobCoordinator:
    def __init__(
        self, memory: MemoryService, driver: OpsMeshDriver, metrics: PlatformMetrics
    ) -> None:
        self.memory, self.driver, self.metrics = memory, driver, metrics
        self._memory_tasks: dict[str, asyncio.Task[Any]] = {}

    async def launch_memory_jobs(self) -> None:
        self._memory_tasks = {
            key: task for key, task in self._memory_tasks.items() if not task.done()
        }
        pending = await self.memory.list_pending_jobs()
        self.metrics.gauge("pending_memory_jobs", len(pending))
        for job in pending:
            if job.id not in self._memory_tasks:
                self._memory_tasks[job.id] = self.driver.create_task(
                    self._run_memory_job(job.id), name=f"memory:{job.id}"
                )

    async def _run_memory_job(self, job_id: str) -> None:
        started = perf_counter()
        try:
            job = await self.memory.run_job(job_id)
            outcome = "success" if job.status in {"succeeded", "no_change"} else "failure"
            self.metrics.increment("memory_jobs", outcome)
        except Exception:
            self.metrics.increment("memory_jobs", "failure")
            raise
        finally:
            self.metrics.observe("memory_duration_seconds", perf_counter() - started)
