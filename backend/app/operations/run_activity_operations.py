from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    OperationsRunActivityResponse,
    RunActivityOldestRunResponse,
    RunActivityPhaseBucketResponse,
)
from backend.app.operations.utils import ensure_aware_utc
from backend.app.runs.activity import run_activity
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task


class RunActivityOperationsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def run_activity_payload(
        self,
        workspace_id: UUID,
        *,
        team_id: UUID | None = None,
        scan_limit: int = 500,
    ) -> OperationsRunActivityResponse:
        now = datetime.now(UTC)
        scan_limit = max(1, min(scan_limit, 1_000))
        statement = _active_runs_statement(workspace_id)
        if team_id is not None:
            statement = statement.join(Task, Task.id == AgentRun.task_id).where(
                Task.workspace_id == workspace_id,
                Task.agent_team_id == team_id,
            )
        active_runs_subquery = statement.order_by(None).subquery()
        total_active_runs = int(
            self._session.scalar(select(func.count()).select_from(active_runs_subquery)) or 0
        )
        status_counts = {
            str(status): int(count)
            for status, count in self._session.execute(
                select(active_runs_subquery.c.status, func.count()).group_by(
                    active_runs_subquery.c.status
                )
            )
        }
        runs = self._session.scalars(
            statement.order_by(AgentRun.updated_at.asc(), AgentRun.created_at.asc()).limit(
                scan_limit
            )
        ).all()
        latest_events = self._latest_run_events_by_run_id(workspace_id, [run.id for run in runs])
        phases, oldest_active_run = _phase_buckets(runs, latest_events, now)
        return OperationsRunActivityResponse(
            generated_at=now,
            team_id=team_id,
            total_active_runs=total_active_runs,
            scanned_active_runs=len(runs),
            truncated=total_active_runs > len(runs),
            status_counts=dict(sorted(status_counts.items())),
            phases=phases,
            oldest_active_run=oldest_active_run,
        )

    def _latest_run_events_by_run_id(
        self,
        workspace_id: UUID,
        run_ids: list[UUID],
    ) -> dict[UUID, RunEvent]:
        if not run_ids:
            return {}
        latest_sequences = (
            select(
                RunEvent.agent_run_id.label("agent_run_id"),
                func.max(RunEvent.sequence).label("latest_sequence"),
            )
            .where(
                RunEvent.workspace_id == workspace_id,
                RunEvent.agent_run_id.in_(run_ids),
            )
            .group_by(RunEvent.agent_run_id)
            .subquery()
        )
        events = self._session.scalars(
            select(RunEvent).join(
                latest_sequences,
                (RunEvent.agent_run_id == latest_sequences.c.agent_run_id)
                & (RunEvent.sequence == latest_sequences.c.latest_sequence),
            )
        ).all()
        return {event.agent_run_id: event for event in events}


def _active_runs_statement(workspace_id: UUID):
    active_statuses = (
        RunStatus.QUEUED.value,
        RunStatus.RUNNING.value,
        RunStatus.WAITING_RUNTIME.value,
        RunStatus.WAITING_APPROVAL.value,
    )
    return select(AgentRun).where(
        AgentRun.workspace_id == workspace_id,
        AgentRun.status.in_(active_statuses),
    )


def _phase_buckets(
    runs: list[AgentRun],
    latest_events: dict[UUID, RunEvent],
    now: datetime,
) -> tuple[list[RunActivityPhaseBucketResponse], RunActivityOldestRunResponse | None]:
    buckets: dict[str, dict[str, object]] = {}
    oldest_active_run: RunActivityOldestRunResponse | None = None
    for run in runs:
        latest_event = latest_events.get(run.id)
        activity = run_activity(run, latest_event)
        phase = str(activity["phase"])
        oldest_run = run_activity_oldest_response(
            run,
            latest_event=latest_event,
            activity=activity,
            now=now,
        )
        bucket = buckets.setdefault(
            phase,
            {
                "label": activity["label"],
                "count": 0,
                "oldest_run": None,
                "recommended_action": activity["recommended_action"],
            },
        )
        bucket["count"] = int(bucket["count"]) + 1
        current_oldest = bucket.get("oldest_run")
        if not isinstance(current_oldest, RunActivityOldestRunResponse) or (
            oldest_run.age_seconds > current_oldest.age_seconds
        ):
            bucket["oldest_run"] = oldest_run
        if oldest_active_run is None or oldest_run.age_seconds > oldest_active_run.age_seconds:
            oldest_active_run = oldest_run
    return [
        RunActivityPhaseBucketResponse(
            phase=phase,
            label=str(values["label"]),
            count=int(values["count"]),
            oldest_age_seconds=(
                values["oldest_run"].age_seconds
                if isinstance(values["oldest_run"], RunActivityOldestRunResponse)
                else None
            ),
            oldest_run=(
                values["oldest_run"]
                if isinstance(values["oldest_run"], RunActivityOldestRunResponse)
                else None
            ),
            recommended_action=(
                str(values["recommended_action"])
                if values.get("recommended_action") is not None
                else None
            ),
        )
        for phase, values in sorted(buckets.items())
    ], oldest_active_run


def run_activity_oldest_response(
    run: AgentRun,
    *,
    latest_event: RunEvent | None,
    activity: dict[str, object],
    now: datetime,
) -> RunActivityOldestRunResponse:
    last_activity_at = ensure_aware_utc(cast(datetime, activity["since"]))
    return RunActivityOldestRunResponse(
        run_id=run.id,
        task_id=run.task_id,
        task_step_id=run.task_step_id,
        agent_profile_id=run.agent_profile_id,
        runtime_id=run.runtime_id,
        runtime_space_id=run.runtime_space_id,
        status=run.status,
        latest_event_type=latest_event.event_type if latest_event is not None else None,
        age_seconds=max(0, int((now - last_activity_at).total_seconds())),
        started_at=ensure_aware_utc(run.started_at) if run.started_at is not None else None,
        last_activity_at=last_activity_at,
    )
