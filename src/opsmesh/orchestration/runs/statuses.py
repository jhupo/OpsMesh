from __future__ import annotations

from opsmesh.orchestration.runs.state import RunStatus

ACTIVE_RUN_STATUS_VALUES = (
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_APPROVAL.value,
    RunStatus.WAITING_SUBWORKFLOW.value,
)

TERMINAL_RUN_STATUS_VALUES = (
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
)

WORKLOAD_RUN_STATUS_VALUES = ACTIVE_RUN_STATUS_VALUES

ACTIVE_RUN_STATUSES = frozenset(ACTIVE_RUN_STATUS_VALUES)
