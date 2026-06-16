from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    StaleRunRecoveryItemResponse,
    StaleRunRecoveryResponse,
)
from backend.app.audit.service import AuditService
from backend.app.core.trace_context import current_trace_metadata
from backend.app.operations.models import WorkerLease
from backend.app.operations.stale_run_domain import (
    non_empty_string_or_none,
    normalized_stale_run_statuses,
    stale_run_failure_message,
)
from backend.app.operations.stale_run_queries import StaleRunQueryService
from backend.app.operations.worker_lifecycle import (
    RUNNING_LEASE_STATUSES,
    append_worker_lifecycle_events,
    worker_lifecycle_event,
)
from backend.app.orchestration.run_control import RunControlService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.status import RunStatus
from backend.app.workers.jobs import JobType
from backend.app.workers.queue import RedisQueue


class StaleRunRecoveryService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder or RedisKeyBuilder("opsmesh")

    def recover(
        self,
        workspace_id: UUID,
        *,
        actor_user_id: UUID,
        stale_after_seconds: int,
        statuses: list[str],
        limit: int,
        queue_name: str = "agent_runs",
        reason: str | None = None,
    ) -> StaleRunRecoveryResponse:
        now = datetime.now(UTC)
        normalized_statuses = normalized_stale_run_statuses(statuses)
        runs = StaleRunQueryService(self._session).stale_runs(
            workspace_id,
            stale_after_seconds=stale_after_seconds,
            statuses=normalized_statuses,
            limit=limit,
            now=now,
        )
        queue = RedisQueue(self._redis, self._keys, queue_name) if self._redis is not None else None
        run_orchestration = RunOrchestrationService(self._session, queue=queue)
        run_control = RunControlService(
            session=self._session,
            enqueue_run=run_orchestration.enqueue_run,
        )
        items: list[StaleRunRecoveryItemResponse] = []
        requeued = 0
        failed_closed = 0
        for run in runs:
            previous_status = run.status
            if previous_status == RunStatus.QUEUED.value:
                enqueued = run_control.requeue_stale_run(
                    run,
                    requested_by_user_id=actor_user_id,
                    reason=reason,
                )
                requeued += 1
                items.append(
                    StaleRunRecoveryItemResponse(
                        run_id=run.id,
                        previous_status=RunStatus.QUEUED.value,
                        action="requeued",
                        enqueued=enqueued,
                    )
                )
                continue
            run_control.fail_recovered_run(
                run,
                code="stale_worker_run",
                message=stale_run_failure_message(previous_status),
                retryable=True,
                event_message="Marked failed by stale run recovery control",
            )
            failed_closed += 1
            items.append(
                StaleRunRecoveryItemResponse(
                    run_id=run.id,
                    previous_status=cast(RunStatus, RunStatus(previous_status)).value,
                    action="failed_closed",
                )
            )

        expired_worker_leases = self._expire_worker_leases_for_runs(
            workspace_id=workspace_id,
            run_ids=[run.id for run in runs],
            expired_at=now,
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="worker.stale_runs_recovered",
            target_type="workspace",
            target_id=workspace_id,
            metadata={
                "stale_after_seconds": stale_after_seconds,
                "statuses": sorted(status.value for status in normalized_statuses),
                "limit": limit,
                "queue_name": queue_name,
                "reason": non_empty_string_or_none(reason),
                "scanned_runs": len(runs),
                "requeued_runs": requeued,
                "failed_closed_runs": failed_closed,
                "expired_worker_leases": expired_worker_leases,
            },
        )
        self._session.commit()
        return StaleRunRecoveryResponse(
            workspace_id=workspace_id,
            stale_after_seconds=stale_after_seconds,
            scanned_runs=len(runs),
            requeued_runs=requeued,
            failed_closed_runs=failed_closed,
            expired_worker_leases=expired_worker_leases,
            items=items,
        )

    def _expire_worker_leases_for_runs(
        self,
        *,
        workspace_id: UUID,
        run_ids: list[UUID],
        expired_at: datetime,
    ) -> int:
        if not run_ids:
            return 0
        leases = self._session.scalars(
            select(WorkerLease).where(
                WorkerLease.workspace_id == workspace_id,
                WorkerLease.job_type == JobType.AGENT_RUN.value,
                WorkerLease.resource_id.in_(run_ids),
                WorkerLease.status.in_(RUNNING_LEASE_STATUSES),
            )
        ).all()
        for lease in leases:
            lease.status = "expired"
            lease.finished_at = expired_at
            lease.lease_metadata = append_worker_lifecycle_events(
                dict(lease.lease_metadata or {})
                | {
                    "expired_by": "stale_run_recovery",
                    "expired_at": expired_at.isoformat(),
                },
                [
                    worker_lifecycle_event(
                        "expired",
                        expired_at,
                        attempt=lease.attempt,
                        status="expired",
                        metadata=current_trace_metadata(),
                    )
                ],
            )
        return len(leases)
