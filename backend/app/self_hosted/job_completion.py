from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.self_hosted import JobCompleteRequest
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.self_hosted.events import SelfHostedEventRecorder
from backend.app.self_hosted.jobs import SelfHostedJobFinalizer
from backend.app.self_hosted.models import SelfHostedJobClaim
from backend.app.self_hosted.types import AuthenticatedWorker


class SelfHostedRunCompletionService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._events = SelfHostedEventRecorder(session)
        self._jobs = SelfHostedJobFinalizer(session, self._events)

    def complete_job(
        self,
        auth: AuthenticatedWorker,
        agent_run_id: UUID,
        data: JobCompleteRequest,
    ) -> SelfHostedJobClaim:
        run = self._locked_worker_run(auth, agent_run_id)
        claim = self._locked_claim_for_run(run)
        if claim is None or claim.worker_id != auth.worker.id:
            raise ValueError("Self-hosted job is not claimed by this worker")
        if claim.status in {"completed", "failed"}:
            if claim.status != data.status:
                raise ValueError("Self-hosted job was already completed with a different status")
            return claim
        if claim.status != "claimed":
            raise ValueError("Self-hosted job is not active")
        self._apply_completion(auth, run, claim, data)
        self._session.commit()
        self._session.refresh(claim)
        return claim

    def _locked_worker_run(self, auth: AuthenticatedWorker, agent_run_id: UUID) -> AgentRun:
        run = self._session.scalar(
            select(AgentRun)
            .where(
                AgentRun.id == agent_run_id,
                AgentRun.workspace_id == auth.worker.workspace_id,
                AgentRun.runtime_id == auth.runtime.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run is None:
            raise ValueError("Agent run not found for worker")
        return run

    def _locked_claim_for_run(self, run: AgentRun) -> SelfHostedJobClaim | None:
        return self._session.scalar(
            select(SelfHostedJobClaim)
            .where(
                SelfHostedJobClaim.workspace_id == run.workspace_id,
                SelfHostedJobClaim.agent_run_id == run.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _apply_completion(
        self,
        auth: AuthenticatedWorker,
        run: AgentRun,
        claim: SelfHostedJobClaim,
        data: JobCompleteRequest,
    ) -> None:
        now = datetime.now(UTC)
        claim.status = data.status
        claim.completed_at = now
        run.completed_at = now
        if data.status == "completed":
            run.status = RunStatus.COMPLETED.value
            run.output = data.output or {}
            self._jobs.mark_task_completed_from_run(run, data.output)
        else:
            run.status = RunStatus.FAILED.value
            run.error = data.error or {
                "code": "self_hosted_job_failed",
                "message": "Self-hosted job failed",
            }
            self._jobs.mark_task_failed_from_run(run)
        self._jobs.release_run_reservations(run, released_at=now)
        self._events.append_run_event(
            run,
            f"self_hosted.job_{data.status}",
            f"Self-hosted job {data.status}",
            {
                "claim_id": str(claim.id),
                "worker_id": str(auth.worker.id),
                "runtime_id": str(auth.runtime.id),
            },
        )
        self._events.append_runtime_space_event(
            auth.runtime,
            f"self_hosted.job_{data.status}",
            str(run.id),
            {"agent_run_id": str(run.id), "claim_id": str(claim.id)},
        )
