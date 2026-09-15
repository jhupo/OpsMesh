from __future__ import annotations

from dataclasses import field
from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from backend.app.core.redis.keys import RedisKeyBuilder
from backend.app.core.utils import non_empty_string_or_none
from backend.app.domains.orchestration.runs.control import RunControlService
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.runs.service import RunOrchestrationService
from backend.app.domains.orchestration.runs.state import RunStatus
from backend.app.observability.audit.service import AuditService
from backend.app.runtime.operations.contracts.queue import (
    StaleRunRecoveryItemResponse,
    StaleRunRecoveryResponse,
)
from backend.app.runtime.operations.recovery import (
    StaleRunQueryService,
    normalized_stale_run_statuses,
    stale_run_failure_message,
)
from backend.app.runtime.workers.leases import (
    StaleRunLeaseExpirationService,
)
from backend.app.runtime.workers.queue import RedisQueue


class StaleRunRecoveryCounts:
    requeued: int = 0
    failed_closed: int = 0
    items: list[StaleRunRecoveryItemResponse] = field(default_factory=list)


class StaleRunRecoveryActionExecutor:
    def __init__(
        self,
        *,
        run_control: RunControlService,
        actor_user_id: UUID,
        reason: str | None,
    ) -> None:
        self._run_control = run_control
        self._actor_user_id = actor_user_id
        self._reason = reason

    def recover(self, runs: list[AgentRun]) -> StaleRunRecoveryCounts:
        counts = StaleRunRecoveryCounts()
        for run in runs:
            if run.status == RunStatus.QUEUED.value:
                self._requeue_run(run, counts)
            else:
                self._fail_close_run(run, counts)
        return counts

    def _requeue_run(self, run: AgentRun, counts: StaleRunRecoveryCounts) -> None:
        enqueued = self._run_control.requeue_stale_run(
            run,
            requested_by_user_id=self._actor_user_id,
            reason=self._reason,
        )
        counts.requeued += 1
        counts.items.append(
            StaleRunRecoveryItemResponse(
                run_id=run.id,
                previous_status=RunStatus.QUEUED.value,
                action="requeued",
                enqueued=enqueued,
            )
        )

    def _fail_close_run(self, run: AgentRun, counts: StaleRunRecoveryCounts) -> None:
        previous_status = run.status
        self._run_control.fail_recovered_run(
            run,
            code="stale_worker_run",
            message=stale_run_failure_message(previous_status),
            retryable=True,
            event_message="Marked failed by stale run recovery control",
        )
        counts.failed_closed += 1
        counts.items.append(
            StaleRunRecoveryItemResponse(
                run_id=run.id,
                previous_status=RunStatus(previous_status).value,
                action="failed_closed",
            )
        )


class StaleRunRecoveryAuditRecorder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_recovery(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        stale_after_seconds: int,
        statuses: set[RunStatus],
        limit: int,
        queue_name: str,
        reason: str | None,
        scanned_runs: int,
        counts: StaleRunRecoveryCounts,
        expired_worker_leases: int,
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="worker.stale_runs_recovered",
            target_type="workspace",
            target_id=workspace_id,
            metadata={
                "stale_after_seconds": stale_after_seconds,
                "statuses": sorted(status.value for status in statuses),
                "limit": limit,
                "queue_name": queue_name,
                "reason": non_empty_string_or_none(reason),
                "scanned_runs": scanned_runs,
                "requeued_runs": counts.requeued,
                "failed_closed_runs": counts.failed_closed,
                "expired_worker_leases": expired_worker_leases,
            },
        )


class StaleRunRecoveryService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str],
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder

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
        queue = RedisQueue(self._redis, self._keys, queue_name)
        run_control = RunControlService(
            session=self._session,
            enqueue_run=RunOrchestrationService(self._session, queue=queue).enqueue_run,
        )
        counts = StaleRunRecoveryActionExecutor(
            run_control=run_control,
            actor_user_id=actor_user_id,
            reason=reason,
        ).recover(runs)
        expired_worker_leases = StaleRunLeaseExpirationService(
            self._session
        ).expire_worker_leases_for_runs(
            workspace_id=workspace_id,
            run_ids=[run.id for run in runs],
            expired_at=now,
        )
        StaleRunRecoveryAuditRecorder(self._session).record_recovery(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            stale_after_seconds=stale_after_seconds,
            statuses=normalized_statuses,
            limit=limit,
            queue_name=queue_name,
            reason=reason,
            scanned_runs=len(runs),
            counts=counts,
            expired_worker_leases=expired_worker_leases,
        )
        self._session.commit()
        return StaleRunRecoveryResponse(
            workspace_id=workspace_id,
            stale_after_seconds=stale_after_seconds,
            scanned_runs=len(runs),
            requeued_runs=counts.requeued,
            failed_closed_runs=counts.failed_closed,
            expired_worker_leases=expired_worker_leases,
            items=counts.items,
        )
