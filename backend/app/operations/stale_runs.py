from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    StaleRunDiagnosticResponse,
    StaleRunRecoveryItemResponse,
    StaleRunRecoveryResponse,
    StaleRunsDiagnosticsResponse,
)
from backend.app.audit.service import AuditService
from backend.app.core.trace_context import current_trace_metadata
from backend.app.operations.models import WorkerLease
from backend.app.operations.worker_lifecycle import (
    RUNNING_LEASE_STATUSES,
    append_worker_lifecycle_events,
    worker_lifecycle_event,
)
from backend.app.orchestration.run_control import RunControlService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.workers.jobs import JobType
from backend.app.workers.queue import RedisQueue


class StaleRunOperationsService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder or RedisKeyBuilder("opsmesh")

    def diagnostics(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> StaleRunsDiagnosticsResponse:
        now = datetime.now(UTC)
        normalized_statuses = normalized_stale_run_statuses(statuses)
        runs = self._stale_runs(
            workspace_id,
            stale_after_seconds=stale_after_seconds,
            statuses=normalized_statuses,
            limit=limit,
            now=now,
        )
        leases_by_run_id = self._latest_worker_leases_by_run_id(
            workspace_id,
            [run.id for run in runs],
        )
        items = [
            stale_run_diagnostic_item(
                run,
                now=now,
                lease=leases_by_run_id.get(run.id),
            )
            for run in runs
        ]
        return StaleRunsDiagnosticsResponse(
            generated_at=now,
            stale_after_seconds=stale_after_seconds,
            total=len(items),
            items=items,
        )

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
        runs = self._stale_runs(
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

    def _stale_runs(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int,
        statuses: set[RunStatus],
        limit: int,
        now: datetime,
    ) -> list[AgentRun]:
        cutoff = now - timedelta(seconds=stale_after_seconds)
        candidates = self._session.scalars(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.status.in_([status.value for status in statuses]),
            )
            .order_by(AgentRun.updated_at.asc(), AgentRun.created_at.asc())
            .limit(limit * 3)
        ).all()
        stale_runs = []
        for run in candidates:
            age_anchor = stale_run_age_anchor(run)
            if age_anchor is not None and age_anchor < cutoff:
                stale_runs.append(run)
        stale_runs.sort(key=lambda run: stale_run_age_anchor(run) or run.created_at)
        return stale_runs[:limit]

    def _latest_worker_leases_by_run_id(
        self,
        workspace_id: UUID,
        run_ids: list[UUID],
    ) -> dict[UUID, WorkerLease]:
        if not run_ids:
            return {}
        leases = self._session.scalars(
            select(WorkerLease)
            .where(
                WorkerLease.workspace_id == workspace_id,
                WorkerLease.job_type == JobType.AGENT_RUN.value,
                WorkerLease.resource_id.in_(run_ids),
            )
            .order_by(WorkerLease.started_at.desc(), WorkerLease.created_at.desc())
        ).all()
        latest: dict[UUID, WorkerLease] = {}
        for lease in leases:
            latest.setdefault(lease.resource_id, lease)
        return latest

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


def normalized_stale_run_statuses(statuses: list[str] | None) -> set[RunStatus]:
    allowed = {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_RUNTIME}
    if not statuses:
        return allowed
    normalized: set[RunStatus] = set()
    for status in statuses:
        try:
            run_status = RunStatus(status)
        except ValueError as exc:
            raise ValueError(f"Unsupported stale run status: {status}") from exc
        if run_status not in allowed:
            raise ValueError(f"Unsupported stale run status: {status}")
        normalized.add(run_status)
    return normalized


def stale_run_age_anchor(run: AgentRun) -> datetime | None:
    if run.status == RunStatus.RUNNING.value:
        anchor = run.started_at or run.updated_at or run.created_at
    elif run.status == RunStatus.WAITING_RUNTIME.value:
        anchor = run.updated_at or run.started_at or run.created_at
    elif run.status == RunStatus.QUEUED.value:
        anchor = run.updated_at or run.created_at
    else:
        return None
    return aware_datetime(anchor)


def stale_run_diagnostic_item(
    run: AgentRun,
    *,
    now: datetime,
    lease: WorkerLease | None,
) -> StaleRunDiagnosticResponse:
    anchor = stale_run_age_anchor(run) or aware_datetime(run.created_at)
    reason_code, reason_message = stale_run_reason(run.status)
    lease_started_at = aware_datetime(lease.started_at) if lease is not None else None
    return StaleRunDiagnosticResponse(
        run_id=run.id,
        status=RunStatus(run.status).value,
        stale_reason_code=reason_code,
        stale_reason_message=reason_message,
        age_seconds=max(0, int((now - anchor).total_seconds())),
        created_at=aware_datetime(run.created_at),
        updated_at=aware_datetime(run.updated_at),
        started_at=aware_datetime(run.started_at) if run.started_at is not None else None,
        task_id=run.task_id,
        task_step_id=run.task_step_id,
        agent_profile_id=run.agent_profile_id,
        runtime_id=run.runtime_id,
        runtime_space_id=run.runtime_space_id,
        worker_id=lease.worker_id if lease is not None else None,
        worker_lease_status=lease.status if lease is not None else None,
        worker_lease_started_at=lease_started_at,
        worker_lease_age_seconds=max(0, int((now - lease_started_at).total_seconds()))
        if lease_started_at is not None
        else None,
    )


def stale_run_reason(status: str) -> tuple[str, str]:
    if status == RunStatus.QUEUED.value:
        return "stale_queued_run", "Queued run has not been claimed by a worker."
    if status == RunStatus.WAITING_RUNTIME.value:
        return "stale_waiting_runtime_run", "Run is waiting for a runtime result too long."
    return "stale_running_run", "Running run has exceeded the worker lease window."


def stale_run_failure_message(status: str) -> str:
    if status == RunStatus.WAITING_RUNTIME.value:
        return "Runtime tool result did not arrive before the recovery window expired"
    return "Worker stopped reporting before the run completed"


def non_empty_string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
