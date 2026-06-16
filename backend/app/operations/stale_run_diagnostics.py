from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    StaleRunDiagnosticResponse,
    StaleRunsDiagnosticsResponse,
)
from backend.app.operations.models import WorkerLease
from backend.app.operations.stale_run_domain import (
    aware_datetime,
    normalized_stale_run_statuses,
    stale_run_age_anchor,
    stale_run_reason,
    worker_lease_age_seconds,
)
from backend.app.operations.stale_run_queries import StaleRunQueryService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus


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
        worker_lease_age_seconds=worker_lease_age_seconds(now, lease),
    )
