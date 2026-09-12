from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from backend.app.api.schemas.operations.queue import StaleRunRecoveryItemResponse
from backend.app.execution.operations.recovery.domain import stale_run_failure_message
from backend.app.orchestration.runs.control import RunControlService
from backend.app.orchestration.runs.models import AgentRun
from backend.app.orchestration.runs.status import RunStatus


@dataclass(slots=True)
class StaleRunRecoveryCounts:
    requeued: int = 0
    failed_closed: int = 0
    items: list[StaleRunRecoveryItemResponse] = field(default_factory=list)


class StaleRunRecoveryActionExecutor:
    def __init__(
        self,
        *,
        run_control: RunControlService,
        actor_user_id: UUID,
        reason: str | None,
    ) -> None:
        self._run_control = run_control
        self._actor_user_id = actor_user_id
        self._reason = reason

    def recover(self, runs: list[AgentRun]) -> StaleRunRecoveryCounts:
        counts = StaleRunRecoveryCounts()
        for run in runs:
            if run.status == RunStatus.QUEUED.value:
                self._requeue_run(run, counts)
            else:
                self._fail_close_run(run, counts)
        return counts

    def _requeue_run(self, run: AgentRun, counts: StaleRunRecoveryCounts) -> None:
        enqueued = self._run_control.requeue_stale_run(
            run,
            requested_by_user_id=self._actor_user_id,
            reason=self._reason,
        )
        counts.requeued += 1
        counts.items.append(
            StaleRunRecoveryItemResponse(
                run_id=run.id,
                previous_status=RunStatus.QUEUED.value,
                action="requeued",
                enqueued=enqueued,
            )
        )

    def _fail_close_run(self, run: AgentRun, counts: StaleRunRecoveryCounts) -> None:
        previous_status = run.status
        self._run_control.fail_recovered_run(
            run,
            code="stale_worker_run",
            message=stale_run_failure_message(previous_status),
            retryable=True,
            event_message="Marked failed by stale run recovery control",
        )
        counts.failed_closed += 1
        counts.items.append(
            StaleRunRecoveryItemResponse(
                run_id=run.id,
                previous_status=RunStatus(previous_status).value,
                action="failed_closed",
            )
        )
