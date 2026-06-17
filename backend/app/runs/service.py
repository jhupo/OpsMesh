from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus, require_run_transition


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
