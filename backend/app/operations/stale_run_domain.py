from __future__ import annotations

from datetime import UTC, datetime

from backend.app.operations.models import WorkerLease
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus


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


def aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def worker_lease_age_seconds(now: datetime, lease: WorkerLease | None) -> int | None:
    if lease is None:
        return None
    lease_started_at = aware_datetime(lease.started_at)
    return max(0, int((now - lease_started_at).total_seconds()))
