from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_RUNTIME = "waiting_runtime"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ALLOWED_RUN_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.WAITING_APPROVAL,
        RunStatus.WAITING_RUNTIME,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.WAITING_RUNTIME: {RunStatus.QUEUED, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.WAITING_APPROVAL: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


def can_transition_run(current: RunStatus, next_status: RunStatus) -> bool:
    return next_status in ALLOWED_RUN_TRANSITIONS[current]


def require_run_transition(current: RunStatus, next_status: RunStatus) -> None:
    if not can_transition_run(current, next_status):
        raise ValueError(f"Invalid run transition: {current.value} -> {next_status.value}")
