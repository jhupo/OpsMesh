"""CAS-protected worker launch ownership and abandoned attempt recovery."""

from __future__ import annotations

import math
import time

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.runtime.events import lifecycle_event

from .metrics import RunMetrics
from .records import RunRecordStore, run_view
from .session_recovery import RunSessionRecovery


class RunWorkerOwnership:
    def __init__(
        self, runs: RunRecordStore, metrics: RunMetrics, sessions: RunSessionRecovery
    ) -> None:
        self.runs, self.metrics, self.sessions = runs, metrics, sessions

    async def recover(self, run_id: str, expected_revision: int) -> AgentRunView:
        """Recover an expired worker without blindly replaying external side effects."""
        record = await self.runs.require(run_id)
        before = record
        if record.revision != expected_revision:
            raise OpsMeshError("conflict", "Run revision changed")
        if record.data["status"] not in {"preparing", "running", "finalizing"}:
            raise OpsMeshError("conflict", "Run does not have an abandoned worker")
        if (record.data.get("lease_expires") or 0) > time.time():
            raise OpsMeshError("conflict", "Worker lease is still valid")
        if record.data.get("cancel_requested"):
            record = await self.runs.update(
                run_id,
                dict(record.data, status="cancelled"),
                expected_revision,
                events=[lifecycle_event(run_id, "cancelled", {})],
            )
            await self.sessions.release(record)
        elif record.data.get("final_outcome"):
            record = await self.runs.update(
                run_id,
                dict(
                    record.data,
                    status="queued",
                    attempt_id=None,
                    worker_launch_token=None,
                    worker_launch_expires=None,
                    queued_at=time.time(),
                ),
                expected_revision,
            )
        else:
            error = OpsMeshError("worker_lost", "Worker lost; side effects require reconciliation")
            record = await self.runs.update(
                run_id,
                dict(record.data, status="failed", error=error.info.model_dump(mode="json")),
                expected_revision,
                events=[lifecycle_event(run_id, "failed", {"code": "worker_lost"})],
            )
            await self.sessions.release(record)
        self.metrics.transition(before, record)
        return run_view(record)

    async def recover_stale(self) -> list[AgentRunView]:
        await self.sessions.reconcile_active()
        recovered = []
        cursor: str | None = None
        cutoff = time.time()
        while page := await self.runs.select_runs(
            statuses=("preparing", "running", "finalizing"), expired_before=cutoff, after_id=cursor
        ):
            cursor = page[-1].id
            for record in page:
                try:
                    recovered.append(await self.recover(record.id, record.revision))
                except OpsMeshError as error:
                    if error.code != "CONFLICT":
                        raise
        return recovered

    async def claim_worker_launch(self, run_id: str, token: str, lease_seconds: float = 30) -> bool:
        if not token or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise OpsMeshError("CONFIG_INVALID", "Launch owner and positive lease are required")
        for _ in range(5):
            record = await self.runs.require(run_id)
            if record.data["status"] not in {"queued", "resuming"}:
                return False
            active = (record.data.get("worker_launch_expires") or 0) > time.time()
            if active and record.data.get("worker_launch_token") != token:
                return False
            try:
                await self.runs.update(
                    run_id,
                    dict(
                        record.data,
                        worker_launch_token=token,
                        worker_launch_expires=time.time() + lease_seconds,
                    ),
                    record.revision,
                )
                return True
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise
        return False

    async def renew_worker_launch(self, run_id: str, token: str, lease_seconds: float = 30) -> bool:
        if not token or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise OpsMeshError("CONFIG_INVALID", "Launch owner and positive lease are required")
        record = await self.runs.require(run_id)
        if (
            record.data["status"] not in {"queued", "resuming"}
            or record.data.get("worker_launch_token") != token
            or (record.data.get("worker_launch_expires") or 0) <= time.time()
        ):
            return False
        try:
            await self.runs.update(
                run_id,
                dict(record.data, worker_launch_expires=time.time() + lease_seconds),
                record.revision,
            )
            return True
        except OpsMeshError as error:
            if error.code != "CONFLICT":
                raise
            return False

    async def worker_exited(
        self, run_id: str, exit_code: int, *, launch_token: str | None = None
    ) -> AgentRunView:
        record = await self.runs.require(run_id)
        if record.data.get("worker_launch_token") != launch_token:
            return run_view(record)
        if exit_code and record.data["status"] in {"queued", "resuming"}:
            error = OpsMeshError(
                "WORKER_START_FAILED",
                "Worker exited before acquiring this run",
                details={"exit_code": exit_code},
            )
            try:
                updated = await self.runs.update(
                    run_id,
                    dict(record.data, status="failed", error=error.info.model_dump(mode="json")),
                    record.revision,
                    events=[lifecycle_event(run_id, "failed", {"code": error.code})],
                )
                self.metrics.transition(record, updated)
                record = updated
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise
                return run_view(await self.runs.require(run_id))
            await self.sessions.release(record)
        return run_view(record)
