from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    OperationsRunActivityResponse,
    OperationsWorkerLifecycleResponse,
    RunActivityOldestRunResponse,
    RunActivityPhaseBucketResponse,
    WorkerLifecycleBucketResponse,
)
from backend.app.core.typing import string_list
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.activity import run_activity
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue

TERMINAL_LEASE_STATUSES = {"completed", "failed", "expired"}


class OperationsActivityService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None,
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder

    def worker_lifecycle_payload(
        self,
        workspace_id: UUID,
        queue_name: str,
    ) -> OperationsWorkerLifecycleResponse:
        now = datetime.now(UTC)
        buckets: dict[str, dict[str, int | list[int] | None]] = {}
        if self._redis is not None:
            queue = RedisQueue(self._redis, self._keys, queue_name)
            for job in queue.peek(limit=1_000):
                if job.workspace_id != workspace_id:
                    continue
                age_seconds = max(
                    0,
                    int((now - _aware_datetime(job.created_at)).total_seconds()),
                )
                for worker_type in _job_worker_types(job):
                    bucket = _worker_lifecycle_bucket(buckets, worker_type)
                    bucket["queued_jobs"] = int(bucket["queued_jobs"]) + 1
                    bucket["oldest_queued_age_seconds"] = _max_optional_int(
                        bucket.get("oldest_queued_age_seconds"),
                        age_seconds,
                    )

        nodes_by_worker_id = {
            node.worker_id: node for node in self._session.scalars(select(WorkerNode)).all()
        }
        leases = self._session.scalars(
            select(WorkerLease).where(WorkerLease.workspace_id == workspace_id)
        ).all()
        for lease in leases:
            worker_type = _lease_worker_type(lease, nodes_by_worker_id)
            bucket = _worker_lifecycle_bucket(buckets, worker_type)
            if lease.status == "running":
                bucket["running_jobs"] = int(bucket["running_jobs"]) + 1
                running_age_seconds = max(
                    0,
                    int((now - _aware_datetime(lease.started_at)).total_seconds()),
                )
                bucket["oldest_running_age_seconds"] = _max_optional_int(
                    bucket.get("oldest_running_age_seconds"),
                    running_age_seconds,
                )
                continue
            if lease.status == "completed":
                bucket["completed_jobs"] = int(bucket["completed_jobs"]) + 1
            elif lease.status == "failed":
                bucket["failed_jobs"] = int(bucket["failed_jobs"]) + 1
            elif lease.status == "retrying":
                bucket["retried_jobs"] = int(bucket["retried_jobs"]) + 1
            elif lease.status == "expired":
                bucket["expired_jobs"] = int(bucket["expired_jobs"]) + 1
            if lease.status in TERMINAL_LEASE_STATUSES and lease.finished_at is not None:
                durations = bucket["durations"]
                if isinstance(durations, list):
                    durations.append(
                        max(
                            0,
                            int(
                                (
                                    _aware_datetime(lease.finished_at)
                                    - _aware_datetime(lease.started_at)
                                ).total_seconds()
                            ),
                        )
                    )

        return OperationsWorkerLifecycleResponse(
            generated_at=now,
            queue_name=queue_name,
            worker_types=[
                _worker_lifecycle_response(worker_type, values)
                for worker_type, values in sorted(buckets.items())
            ],
        )

    def run_activity_payload(
        self,
        workspace_id: UUID,
        *,
        team_id: UUID | None = None,
        scan_limit: int = 500,
    ) -> OperationsRunActivityResponse:
        now = datetime.now(UTC)
        scan_limit = max(1, min(scan_limit, 1_000))
        active_statuses = (
            RunStatus.QUEUED.value,
            RunStatus.RUNNING.value,
            RunStatus.WAITING_RUNTIME.value,
            RunStatus.WAITING_APPROVAL.value,
        )
        statement = select(AgentRun).where(
            AgentRun.workspace_id == workspace_id,
            AgentRun.status.in_(active_statuses),
        )
        if team_id is not None:
            statement = statement.join(Task, Task.id == AgentRun.task_id).where(
                Task.workspace_id == workspace_id,
                Task.agent_team_id == team_id,
            )
        active_runs_subquery = statement.order_by(None).subquery()
        total_active_runs = int(
            self._session.scalar(select(func.count()).select_from(active_runs_subquery))
            or 0
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

        phase_buckets: dict[str, dict[str, object]] = {}
        oldest_active_run: RunActivityOldestRunResponse | None = None
        for run in runs:
            latest_event = latest_events.get(run.id)
            activity = run_activity(run, latest_event)
            phase = str(activity["phase"])
            oldest_run = _run_activity_oldest_response(
                run,
                latest_event=latest_event,
                activity=activity,
                now=now,
            )
            bucket = phase_buckets.setdefault(
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

        phases = [
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
            for phase, values in sorted(phase_buckets.items())
        ]
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


def _job_worker_types(job: JobPayload) -> list[str]:
    worker_types = string_list(job.routing.get("worker_types"))
    return worker_types or ["unrouted"]


def _lease_worker_type(lease: WorkerLease, nodes_by_worker_id: dict[str, WorkerNode]) -> str:
    node = nodes_by_worker_id.get(lease.worker_id)
    if node is not None:
        return node.worker_type
    metadata_worker_type = lease.lease_metadata.get("worker_type")
    return metadata_worker_type if isinstance(metadata_worker_type, str) else "unknown"


def _worker_lifecycle_bucket(
    buckets: dict[str, dict[str, int | list[int] | None]],
    worker_type: str,
) -> dict[str, int | list[int] | None]:
    return buckets.setdefault(
        worker_type,
        {
            "queued_jobs": 0,
            "running_jobs": 0,
            "completed_jobs": 0,
            "failed_jobs": 0,
            "retried_jobs": 0,
            "expired_jobs": 0,
            "oldest_queued_age_seconds": None,
            "oldest_running_age_seconds": None,
            "durations": [],
        },
    )


def _worker_lifecycle_response(
    worker_type: str,
    values: dict[str, int | list[int] | None],
) -> WorkerLifecycleBucketResponse:
    terminal_jobs = (
        int(values["completed_jobs"])
        + int(values["failed_jobs"])
        + int(values["expired_jobs"])
    )
    unsuccessful_jobs = int(values["failed_jobs"]) + int(values["expired_jobs"])
    durations = values["durations"]
    duration_values = durations if isinstance(durations, list) else []
    return WorkerLifecycleBucketResponse(
        worker_type=worker_type,
        queued_jobs=int(values["queued_jobs"]),
        running_jobs=int(values["running_jobs"]),
        completed_jobs=int(values["completed_jobs"]),
        failed_jobs=int(values["failed_jobs"]),
        retried_jobs=int(values["retried_jobs"]),
        expired_jobs=int(values["expired_jobs"]),
        failure_rate=round(unsuccessful_jobs / terminal_jobs, 4) if terminal_jobs > 0 else 0.0,
        average_duration_seconds=int(sum(duration_values) / len(duration_values))
        if duration_values
        else None,
        oldest_queued_age_seconds=cast(int | None, values["oldest_queued_age_seconds"]),
        oldest_running_age_seconds=cast(int | None, values["oldest_running_age_seconds"]),
    )


def _run_activity_oldest_response(
    run: AgentRun,
    *,
    latest_event: RunEvent | None,
    activity: dict[str, object],
    now: datetime,
) -> RunActivityOldestRunResponse:
    last_activity_at = _aware_datetime(cast(datetime, activity["since"]))
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
        started_at=_aware_datetime(run.started_at) if run.started_at is not None else None,
        last_activity_at=last_activity_at,
    )


def _max_optional_int(current: object, candidate: int) -> int:
    return candidate if not isinstance(current, int) else max(current, candidate)


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
