from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from backend.app.domains.orchestration.runs.models import AgentRun


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_RUNTIME = "waiting_runtime"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_SUBWORKFLOW = "waiting_subworkflow"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ALLOWED_RUN_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.QUEUED,
        RunStatus.WAITING_APPROVAL,
        RunStatus.WAITING_RUNTIME,
        RunStatus.WAITING_SUBWORKFLOW,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.WAITING_RUNTIME: {
        RunStatus.QUEUED,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.WAITING_APPROVAL: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.WAITING_SUBWORKFLOW: {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


def can_transition_run(current: RunStatus, next_status: RunStatus) -> bool:
    return next_status in ALLOWED_RUN_TRANSITIONS[current]


def require_run_transition(current: RunStatus, next_status: RunStatus) -> None:
    if not can_transition_run(current, next_status):
        raise ValueError(f"Invalid run transition: {current.value} -> {next_status.value}")


@dataclass(frozen=True)
class RunTransition:
    previous_status: RunStatus
    next_status: RunStatus
    changed: bool


class RunStateService:
    def transition(
        self,
        run: AgentRun,
        next_status: RunStatus,
        *,
        completed_at: datetime | None = None,
        error: dict[str, object] | None = None,
        output: dict[str, object] | None = None,
        started_at: datetime | None = None,
    ) -> RunTransition:
        current_status = RunStatus(run.status)
        if current_status == next_status:
            return RunTransition(current_status, next_status, changed=False)

        require_run_transition(current_status, next_status)
        run.status = next_status.value

        if next_status == RunStatus.RUNNING and run.started_at is None:
            run.started_at = started_at or datetime.now(UTC)
        elif next_status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            run.completed_at = completed_at or datetime.now(UTC)

        if error is not None:
            run.error = error
        if output is not None:
            run.output = output

        return RunTransition(current_status, next_status, changed=True)
