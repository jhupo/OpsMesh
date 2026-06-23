from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from backend.app.api.schemas.operation_control_plane import (
    RunActivityOldestRunResponse,
    RunActivityPhaseBucketResponse,
)
from backend.app.operations.utils import ensure_aware_utc
from backend.app.runs.activity import run_activity
from backend.app.runs.models import AgentRun, RunEvent


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
