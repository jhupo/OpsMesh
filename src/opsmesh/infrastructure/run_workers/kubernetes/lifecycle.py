"""Coordinate one durable Job per Run attempt without replaying an abandoned execution."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.run_workers.kubernetes.client import KubernetesAPI
from opsmesh.infrastructure.run_workers.kubernetes.job_spec import (
    build_job,
    identity,
    owned_identity,
    selector,
)
from opsmesh.infrastructure.run_workers.kubernetes.observation import WorkerDiagnostics, job_status
from opsmesh.infrastructure.run_workers.status import WorkerProcessStatus
from opsmesh.modules.runtime.api import AgentRuntime

logger = logging.getLogger(__name__)


class KubernetesRunWorkers:
    def __init__(
        self,
        runtime: AgentRuntime,
        api: KubernetesAPI,
        *,
        image: str,
        cpu: float,
        memory_mb: int,
        data_directory: Path,
    ) -> None:
        self.runtime, self.api = runtime, api
        self.configuration = api.configuration
        self.image, self.cpu, self.memory_mb = image, cpu, memory_mb
        self.diagnostics = WorkerDiagnostics(data_directory, api)

    async def launch(self, run_id: str) -> None:
        view = await self.runtime.get_run(run_id)
        if view.status not in {"queued", "resuming"} or view.cancel_requested:
            return
        specification = build_job(
            self.configuration,
            run_id=run_id,
            generation=view.attempt,
            image=self.image,
            cpu=self.cpu,
            memory_mb=self.memory_mb,
        )
        _, token = identity(self.configuration, run_id, view.attempt)
        if not await self.runtime.claim_worker_launch(run_id, token):
            return
        try:
            job = await self.api.create_job(specification)
        except OpsMeshError as error:
            if not error.retryable:
                await self.runtime.worker_exited(run_id, 1, launch_token=token)
            raise
        owned_identity(self.configuration, job)
        expected = specification["metadata"]["annotations"]["opsmesh.io/spec-fingerprint"]
        actual = job["metadata"]["annotations"].get("opsmesh.io/spec-fingerprint")
        if actual != expected:
            raise OpsMeshError(
                "CONFLICT", "Existing Job was created with different deployment configuration"
            )

    async def _jobs(self, run_id: str | None = None) -> list[dict[str, Any]]:
        jobs = []
        for job in await self.api.list_jobs(selector(self.configuration, run_id)):
            try:
                owner, _, _ = owned_identity(self.configuration, job)
                if not job.get("metadata", {}).get("uid"):
                    raise OpsMeshError("OWNERSHIP_LOST", "Kubernetes Job has no server UID")
                if run_id is None or owner == run_id:
                    jobs.append(job)
            except OpsMeshError:
                # Labels are a discovery index, never sufficient authority to delete a resource.
                logger.warning("Ignoring Kubernetes Job with inconsistent deployment ownership")
        return jobs

    async def poll(self, run_id: str) -> WorkerProcessStatus | None:
        jobs = await self._jobs(run_id)
        if not jobs:
            return self.diagnostics.saved_status(run_id)
        job = max(jobs, key=lambda value: owned_identity(self.configuration, value)[2])
        owner, token, _ = owned_identity(self.configuration, job)
        return job_status(job, owner, token)

    async def logs(self, run_id: str) -> str:
        entries = [self.diagnostics.saved_logs(run_id)]
        for job in await self._jobs(run_id):
            content, _ = await self.diagnostics.collect(job)
            entries.append(content)
        return self.diagnostics.bounded("\n".join(filter(None, entries)))

    async def cancel(self, run_id: str) -> None:
        # Persist cancellation before deleting resources; a delayed Pod cannot execute the Run.
        await self.runtime.cancel(run_id)
        for job in await self._jobs(run_id):
            await self._retire(job, cancelled=True)

    async def reap(self) -> None:
        capacity = asyncio.Semaphore(8)

        async def collect(job: dict[str, Any]) -> None:
            async with capacity:
                try:
                    await self._reap_job(job)
                except OpsMeshError as error:
                    # One failed log endpoint must not prevent other launch leases, cancellations
                    # or queued Runs from making progress. Its Job remains for the next scan/TTL.
                    logger.warning("Kubernetes Job collection deferred: %s", error.code)

        await asyncio.gather(*(collect(job) for job in await self._jobs()))

    async def _reap_job(self, job: dict[str, Any]) -> None:
        run_id, token, generation = owned_identity(self.configuration, job)
        try:
            view = await self.runtime.get_run(run_id)
        except OpsMeshError as error:
            if error.code != "NOT_FOUND":
                raise
            await self._retire(job, cancelled=True)
            return
        if view.cancel_requested or view.status == "cancelled":
            await self._retire(job, cancelled=True)
        elif job_status(job, run_id, token).status == "exited":
            await self._retire(job)
        elif view.status in {"queued", "resuming"} and generation == view.attempt:
            # A restarted coordinator can renew the same logical launch owner, including while
            # the scheduler is waiting for image downloads or capacity. CAS still gates execution.
            await self.runtime.claim_worker_launch(run_id, token)

    async def _retire(self, job: dict[str, Any], *, cancelled: bool = False) -> None:
        run_id, token, generation = owned_identity(self.configuration, job)
        status = job_status(job, run_id, token)
        logs, container_exit = await self.diagnostics.collect(job)
        code = container_exit if container_exit is not None else status.exit_code
        if cancelled:
            status = status.model_copy(
                update={"status": "exited", "exit_code": code, "reason": "Cancelled"}
            )
        else:
            view = await self.runtime.get_run(run_id)
            # An exit-zero process which never acquired the Run is also a startup failure.
            if view.status in {"queued", "resuming"} and generation == view.attempt:
                code = code or 1
            status = status.model_copy(update={"exit_code": code})
            if code:
                await self.runtime.worker_exited(run_id, code, launch_token=token)
        self.diagnostics.persist(status, generation, logs)
        await self.api.delete_job(job["metadata"]["name"], job["metadata"]["uid"])

    async def close(self) -> None:
        # Jobs outlive an individual coordinator. Cancelling them here breaks rolling upgrades.
        await self.api.close()
