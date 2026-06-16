from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_queue import StaleRunRecoveryResponse
from backend.app.operations.stale_run_domain import normalized_stale_run_statuses
from backend.app.operations.stale_run_lease_expiration import StaleRunLeaseExpirationService
from backend.app.operations.stale_run_queries import StaleRunQueryService
from backend.app.operations.stale_run_recovery_actions import StaleRunRecoveryActionExecutor
from backend.app.operations.stale_run_recovery_audit import StaleRunRecoveryAuditRecorder
from backend.app.orchestration.run_control import RunControlService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.queue.redis_queue import RedisQueue


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
