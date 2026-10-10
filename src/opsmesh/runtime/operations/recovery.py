from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.runs.statuses import STALE_RECOVERABLE_RUN_STATUS_VALUES
from opsmesh.runtime.operations.contracts.queue import (
    StaleRunDiagnosticResponse,
    StaleRunsDiagnosticsResponse,
)
from opsmesh.runtime.queues.contracts import JobType
from opsmesh.runtime.workers.models import WorkerLease
from opsmesh.shared.utils import ensure_aware_utc


def normalized_stale_run_statuses(statuses: list[str] | None) -> set[RunStatus]:
    allowed = {RunStatus(status) for status in STALE_RECOVERABLE_RUN_STATUS_VALUES}
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
    if run.status == RunStatus.WAITING_RUNTIME.value and run.input.get("runtime_capacity_waiting"):
        return None
    if run.status == RunStatus.RUNNING.value:
        anchor = run.started_at or run.updated_at or run.created_at
    elif run.status == RunStatus.WAITING_RUNTIME.value:
        anchor = run.updated_at or run.started_at or run.created_at
    elif run.status == RunStatus.QUEUED.value:
        anchor = run.updated_at or run.created_at
    else:
        return None
    return ensure_aware_utc(anchor)


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


def worker_lease_age_seconds(now: datetime, lease: WorkerLease | None) -> int | None:
    if lease is None:
        return None
    lease_started_at = ensure_aware_utc(lease.started_at)
    return max(0, int((now - lease_started_at).total_seconds()))


class StaleRunDiagnosticsService:
    def __init__(self, session: Session) -> None:
        self._session = session

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
        query = StaleRunQueryService(self._session)
        runs = query.stale_runs(
            workspace_id,
            stale_after_seconds=stale_after_seconds,
            statuses=normalized_statuses,
            limit=limit,
            now=now,
        )
        leases_by_run_id = query.latest_worker_leases_by_run_id(
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


def stale_run_diagnostic_item(
    run: AgentRun,
    *,
    now: datetime,
    lease: WorkerLease | None,
) -> StaleRunDiagnosticResponse:
    anchor = stale_run_age_anchor(run) or ensure_aware_utc(run.created_at)
    reason_code, reason_message = stale_run_reason(run.status)
    lease_started_at = ensure_aware_utc(lease.started_at) if lease is not None else None
    return StaleRunDiagnosticResponse(
        run_id=run.id,
        status=RunStatus(run.status).value,
        stale_reason_code=reason_code,
        stale_reason_message=reason_message,
        age_seconds=max(0, int((now - anchor).total_seconds())),
        created_at=ensure_aware_utc(run.created_at),
        updated_at=ensure_aware_utc(run.updated_at),
        started_at=ensure_aware_utc(run.started_at) if run.started_at is not None else None,
        task_id=run.task_id,
        task_step_id=run.task_step_id,
        agent_profile_id=run.agent_profile_id,
        runtime_id=run.runtime_id,
        runtime_space_id=run.runtime_space_id,
        worker_id=lease.worker_id if lease is not None else None,
        worker_lease_status=lease.status if lease is not None else None,
        worker_lease_started_at=lease_started_at,
        worker_lease_age_seconds=worker_lease_age_seconds(now, lease),
    )


class StaleRunQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def stale_runs(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int,
        statuses: set[RunStatus],
        limit: int,
        now: datetime,
        lock: bool = False,
    ) -> list[AgentRun]:
        cutoff = now - timedelta(seconds=stale_after_seconds)
        anchor = case(
            (
                AgentRun.status == "running",
                func.coalesce(AgentRun.started_at, AgentRun.updated_at, AgentRun.created_at),
            ),
            else_=func.coalesce(AgentRun.updated_at, AgentRun.created_at),
        )
        statement = (
            select(AgentRun)
            .where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.status.in_([status.value for status in statuses]),
                anchor < cutoff,
            )
            .order_by(anchor, AgentRun.id)
            .limit(limit)
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update(skip_locked=True)
        return list(self._session.scalars(statement).all())

    def latest_worker_leases_by_run_id(
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
