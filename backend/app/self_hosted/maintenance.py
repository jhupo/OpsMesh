from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.events import SelfHostedEventRecorder
from backend.app.self_hosted.jobs import SelfHostedJobFinalizer
from backend.app.self_hosted.models import SelfHostedWorker
from backend.app.self_hosted.types import WorkerTrustCleanupResult


class SelfHostedMaintenanceService:
    def __init__(
        self,
        session: Session,
        events: SelfHostedEventRecorder,
        jobs: SelfHostedJobFinalizer,
    ) -> None:
        self._session = session
        self._events = events
        self._jobs = jobs

    def cleanup_stale_workers(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int = 600,
        quarantine_after_seconds: int | None = None,
        job_claim_stale_after_seconds: int = 900,
        mcp_job_stale_after_seconds: int = 900,
    ) -> WorkerTrustCleanupResult:
        now = datetime.now(UTC)
        degraded_cutoff = now - timedelta(seconds=stale_after_seconds)
        job_claim_cutoff = now - timedelta(seconds=job_claim_stale_after_seconds)
        mcp_job_cutoff = now - timedelta(seconds=mcp_job_stale_after_seconds)
        quarantine_after_seconds = quarantine_after_seconds or stale_after_seconds * 3
        quarantine_after_seconds = max(quarantine_after_seconds, stale_after_seconds)
        quarantine_cutoff = now - timedelta(seconds=quarantine_after_seconds)
        stale_workers = self._session.scalars(
            select(SelfHostedWorker).where(
                SelfHostedWorker.workspace_id == workspace_id,
                SelfHostedWorker.status.in_(["online", "degraded"]),
                SelfHostedWorker.last_heartbeat_at.is_not(None),
                SelfHostedWorker.last_heartbeat_at < degraded_cutoff,
            )
        ).all()
        degraded = 0
        quarantined = 0
        for worker in stale_workers:
            runtime = self._session.get(WorkspaceRuntime, worker.workspace_runtime_id)
            last_heartbeat_at = _as_utc(worker.last_heartbeat_at)
            if last_heartbeat_at is None:
                continue
            should_quarantine = last_heartbeat_at <= quarantine_cutoff
            if should_quarantine:
                if worker.status != "quarantined":
                    quarantined += 1
                worker.status = "quarantined"
                if runtime is not None:
                    runtime.status = "quarantined"
                    runtime.connection_status = "offline"
                    metadata = {
                        "worker_id": str(worker.id),
                        "last_heartbeat_at": last_heartbeat_at.isoformat(),
                        "stale_after_seconds": stale_after_seconds,
                        "quarantine_after_seconds": quarantine_after_seconds,
                        "quarantined_at": now.isoformat(),
                    }
                    self._events.append_runtime_event(
                        runtime,
                        "self_hosted.worker_quarantined",
                        worker.machine_id,
                        metadata,
                    )
                    self._events.append_runtime_space_event(
                        runtime,
                        "self_hosted.worker_quarantined",
                        worker.machine_id,
                        metadata,
                    )
                continue
            if worker.status != "degraded":
                degraded += 1
            worker.status = "degraded"
            if runtime is not None:
                runtime.connection_status = "degraded"
                metadata = {
                    "worker_id": str(worker.id),
                    "last_heartbeat_at": last_heartbeat_at.isoformat(),
                    "stale_after_seconds": stale_after_seconds,
                    "degraded_at": now.isoformat(),
                }
                self._events.append_runtime_event(
                    runtime,
                    "self_hosted.worker_degraded",
                    worker.machine_id,
                    metadata,
                )
                self._events.append_runtime_space_event(
                    runtime,
                    "self_hosted.worker_degraded",
                    worker.machine_id,
                    metadata,
                )
        expired_job_claims = self._jobs.expire_stale_job_claims(
            workspace_id,
            job_claim_cutoff,
            now,
        )
        expired_mcp_jobs = self._jobs.expire_stale_mcp_jobs(
            workspace_id,
            mcp_job_cutoff,
            now,
        )
        self._session.commit()
        return WorkerTrustCleanupResult(
            degraded=degraded,
            quarantined=quarantined,
            expired_job_claims=expired_job_claims,
            expired_mcp_jobs=expired_mcp_jobs,
        )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
