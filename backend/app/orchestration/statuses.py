from __future__ import annotations

from backend.app.runs.status import RunStatus

ORCHESTRATION_ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
}
