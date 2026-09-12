from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_control_plane import (
    OperationsRunActivityResponse,
    RunActivityOldestRunResponse,
    RunActivityPhaseBucketResponse,
)
from backend.app.operations.utils import ensure_aware_utc
from backend.app.orchestration.workflows.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.runs.activity import run_activity
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task


@dataclass(slots=True)
class RunActivityBucket:
    phase: str
    label: str
    recommended_action: str | None
    count: int = 0
    oldest_run: RunActivityOldestRunResponse | None = None

    def add(self, oldest_run: RunActivityOldestRunResponse) -> None:
        self.count += 1
        if self.oldest_run is None or oldest_run.age_seconds > self.oldest_run.age_seconds:
            self.oldest_run = oldest_run

    def response(self) -> RunActivityPhaseBucketResponse:
        return RunActivityPhaseBucketResponse(
            phase=self.phase,
            label=self.label,
            count=self.count,
            oldest_age_seconds=self.oldest_run.age_seconds if self.oldest_run else None,
            oldest_run=self.oldest_run,
            recommended_action=self.recommended_action,
        )


class RunActivityBucketAccumulator:
    def __init__(self, now: datetime) -> None:
        self._now = now
        self._buckets: dict[str, RunActivityBucket] = {}
        self.oldest_active_run: RunActivityOldestRunResponse | None = None

    def add_run(self, run: AgentRun, latest_event: RunEvent | None) -> None:
        activity = run_activity(run, latest_event)
        phase = str(activity["phase"])
        bucket = self._buckets.setdefault(
            phase,
            RunActivityBucket(
                phase=phase,
                label=str(activity["label"]),
                recommended_action=(
                    str(activity["recommended_action"])
                    if activity.get("recommended_action") is not None
                    else None
                ),
            ),
        )
        oldest_run = run_activity_oldest_response(
            run,
            latest_event=latest_event,
            activity=activity,
            now=self._now,
        )
        bucket.add(oldest_run)
        if (
            self.oldest_active_run is None
            or oldest_run.age_seconds > self.oldest_active_run.age_seconds
        ):
            self.oldest_active_run = oldest_run

    def phase_responses(self) -> list[RunActivityPhaseBucketResponse]:
        return [bucket.response() for _, bucket in sorted(self._buckets.items())]


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







@dataclass(frozen=True, slots=True)
class ActiveRunPage:
    total_active_runs: int
    status_counts: dict[str, int]
    runs: list[AgentRun]


class RunActivityQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def active_run_page(
        self,
        workspace_id: UUID,
        *,
        team_id: UUID | None,
        scan_limit: int,
    ) -> ActiveRunPage:
        statement = active_runs_statement(workspace_id, team_id=team_id)
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
        return ActiveRunPage(
            total_active_runs=total_active_runs,
            status_counts=dict(sorted(status_counts.items())),
            runs=list(runs),
        )

    def latest_run_events_by_run_id(
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


def active_runs_statement(
    workspace_id: UUID,
    *,
    team_id: UUID | None,
) -> Select[tuple[AgentRun]]:
    statement = select(AgentRun).where(
        AgentRun.workspace_id == workspace_id,
            AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
    )
    if team_id is None:
        return statement
    return statement.join(Task, Task.id == AgentRun.task_id).where(
        Task.workspace_id == workspace_id,
        Task.agent_team_id == team_id,
    )







class RunActivityPayloadService:
    def __init__(self, session: Session) -> None:
        self._queries = RunActivityQueryService(session)

    def run_activity_payload(
        self,
        workspace_id: UUID,
        *,
        team_id: UUID | None = None,
        scan_limit: int = 500,
    ) -> OperationsRunActivityResponse:
        now = datetime.now(UTC)
        page = self._queries.active_run_page(
            workspace_id,
            team_id=team_id,
            scan_limit=scan_limit,
        )
        latest_events = self._queries.latest_run_events_by_run_id(
            workspace_id,
            [run.id for run in page.runs],
        )
        buckets = RunActivityBucketAccumulator(now)
        for run in page.runs:
            buckets.add_run(run, latest_events.get(run.id))
        return OperationsRunActivityResponse(
            generated_at=now,
            team_id=team_id,
            total_active_runs=page.total_active_runs,
            scanned_active_runs=len(page.runs),
            truncated=page.total_active_runs > len(page.runs),
            status_counts=page.status_counts,
            phases=buckets.phase_responses(),
            oldest_active_run=buckets.oldest_active_run,
        )
