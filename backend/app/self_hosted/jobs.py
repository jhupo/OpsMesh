from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.runtime_spaces.service import RuntimeSpaceService
from backend.app.self_hosted.events import SelfHostedEventRecorder
from backend.app.self_hosted.models import (
    RuntimeCredential,
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus
from backend.app.workspaces.quotas import WorkspaceQuotaService


class SelfHostedJobFinalizer:
    def __init__(self, session: Session, events: SelfHostedEventRecorder) -> None:
        self._session = session
        self._events = events

    def release_run_reservations(
        self,
        run: AgentRun,
        *,
        released_at: datetime | None = None,
    ) -> None:
        WorkspaceQuotaService(self._session).release_reservations_for_run(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            released_at=released_at,
        )
        RuntimeSpaceService(self._session).release_reservations_for_run(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            released_at=released_at,
        )

    def job_claim_for_run(self, run: AgentRun) -> SelfHostedJobClaim | None:
        return self._session.scalar(
            select(SelfHostedJobClaim).where(
                SelfHostedJobClaim.workspace_id == run.workspace_id,
                SelfHostedJobClaim.agent_run_id == run.id,
            )
        )

    def mark_task_completed_from_run(
        self,
        run: AgentRun,
        output: dict[str, object] | None,
    ) -> None:
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = "completed"
                step.result_summary = _summary_from_payload(output)
        if run.task_id is None:
            return
        task = self._session.get(Task, run.task_id)
        if task is not None and task.workspace_id == run.workspace_id:
            TaskStateService().transition(
                task,
                TaskStatus.COMPLETED,
                completed_at=run.completed_at,
                final_output=output or {},
            )

    def mark_task_failed_from_run(self, run: AgentRun) -> None:
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = "failed"
        if run.task_id is None:
            return
        task = self._session.get(Task, run.task_id)
        if task is not None and task.workspace_id == run.workspace_id:
            TaskStateService().transition(task, TaskStatus.FAILED, completed_at=run.completed_at)

    def active_claims_for_worker(self, worker: SelfHostedWorker) -> list[SelfHostedJobClaim]:
        return list(
            self._session.scalars(
                select(SelfHostedJobClaim).where(
                    SelfHostedJobClaim.workspace_id == worker.workspace_id,
                    SelfHostedJobClaim.worker_id == worker.id,
                    SelfHostedJobClaim.status == "claimed",
                )
            ).all()
        )

    def latest_credential_for_runtime(self, runtime_id: UUID) -> RuntimeCredential | None:
        return self._session.scalar(
            select(RuntimeCredential)
            .where(RuntimeCredential.workspace_runtime_id == runtime_id)
            .order_by(RuntimeCredential.created_at.desc())
            .limit(1)
        )

    def close_active_claims_for_worker(
        self,
        worker: SelfHostedWorker,
        *,
        claim_status: str,
        error_code: str,
        error_message: str,
        event_type: str,
        event_message: str,
        now: datetime,
        reason: str,
    ) -> tuple[int, int]:
        affected_claims = 0
        affected_runs = 0
        for claim in self.active_claims_for_worker(worker):
            affected_claims += 1
            claim.status = claim_status
            claim.completed_at = now
            run = self._session.get(AgentRun, claim.agent_run_id)
            if run is None or run.workspace_id != worker.workspace_id:
                continue
            affected_runs += 1
            if run.status in {
                RunStatus.QUEUED.value,
                RunStatus.RUNNING.value,
                RunStatus.WAITING_APPROVAL.value,
            }:
                run.status = RunStatus.FAILED.value
                run.completed_at = now
                run.error = {
                    "code": error_code,
                    "message": error_message,
                    "retryable": claim_status != "revoked",
                }
                self.mark_task_failed_from_run(run)
                self._events.append_run_event(
                    run,
                    event_type,
                    event_message,
                    {
                        "worker_id": str(worker.id),
                        "claim_id": str(claim.id),
                        "reason": reason,
                    },
                )
            self.release_run_reservations(run, released_at=now)
        return affected_claims, affected_runs

    def expire_stale_job_claims(
        self,
        workspace_id: UUID,
        cutoff: datetime,
        now: datetime,
    ) -> int:
        stale_claims = self._session.scalars(
            select(SelfHostedJobClaim).where(
                SelfHostedJobClaim.workspace_id == workspace_id,
                SelfHostedJobClaim.status == "claimed",
                SelfHostedJobClaim.claimed_at < cutoff,
            )
        ).all()
        expired = 0
        for claim in stale_claims:
            expired += 1
            claim.status = "expired"
            claim.completed_at = now
            run = self._session.get(AgentRun, claim.agent_run_id)
            if run is None or run.workspace_id != workspace_id:
                continue
            if run.status in {
                RunStatus.QUEUED.value,
                RunStatus.RUNNING.value,
                RunStatus.WAITING_APPROVAL.value,
            }:
                run.status = RunStatus.FAILED.value
                run.completed_at = now
                run.error = {
                    "code": "self_hosted_job_claim_expired",
                    "message": "Self-hosted job claim expired before completion.",
                    "retryable": True,
                }
                self.mark_task_failed_from_run(run)
                self._events.append_run_event(
                    run,
                    "self_hosted.job_claim_expired",
                    "Self-hosted job claim expired before completion.",
                    {
                        "claim_id": str(claim.id),
                        "worker_id": str(claim.worker_id),
                        "expired_at": now.isoformat(),
                    },
                )
            self.release_run_reservations(run, released_at=now)
        return expired

    def expire_stale_mcp_jobs(
        self,
        workspace_id: UUID,
        cutoff: datetime,
        now: datetime,
    ) -> int:
        stale_jobs = self._session.scalars(
            select(SelfHostedMcpJob).where(
                SelfHostedMcpJob.workspace_id == workspace_id,
                SelfHostedMcpJob.status.in_(["queued", "claimed"]),
                SelfHostedMcpJob.created_at < cutoff,
            )
        ).all()
        expired = 0
        for job in stale_jobs:
            expired += 1
            job.status = "expired"
            job.completed_at = now
            job.error_payload = {
                "code": "self_hosted_mcp_job_expired",
                "message": "Self-hosted MCP job expired before completion.",
                "retryable": True,
            }
            run = self._session.get(AgentRun, job.agent_run_id)
            if run is None or run.workspace_id != workspace_id:
                continue
            self.record_mcp_job_completion_for_run(run, job)
            self._events.append_run_event(
                run,
                "self_hosted.mcp_job_expired",
                job.tool_name,
                {
                    "mcp_job_id": str(job.id),
                    "mcp_server_id": str(job.mcp_server_id),
                    "expired_at": now.isoformat(),
                },
            )
        return expired

    def record_mcp_job_completion_for_run(
        self,
        run: AgentRun,
        job: SelfHostedMcpJob,
    ) -> None:
        run_input = dict(run.input) if isinstance(run.input, dict) else {}
        pending_results = run_input.get("pending_tool_results")
        if not isinstance(pending_results, list):
            pending_results = []
        pending_results.append(
            {
                "kind": "mcp",
                "mcp_job_id": str(job.id),
                "mcp_server_id": str(job.mcp_server_id),
                "tool_name": job.tool_name,
                "status": job.status,
                "response": job.response_payload,
                "error": job.error_payload,
                "completed_at": dt_iso(job.completed_at),
            }
        )
        run_input["pending_tool_results"] = pending_results
        run.input = run_input
        if job.status == "completed" and run.status == RunStatus.WAITING_RUNTIME.value:
            run.status = RunStatus.QUEUED.value
        elif job.status in {"failed", "expired"} and run.status == RunStatus.WAITING_RUNTIME.value:
            run.status = RunStatus.FAILED.value
            run.error = job.error_payload or {
                "code": "self_hosted_mcp_job_failed",
                "message": "Self-hosted MCP job failed",
                "retryable": True,
            }
            run.completed_at = datetime.now(UTC)


def dt_iso(value: datetime | None) -> str | None:
    utc_value = _as_utc(value)
    return utc_value.isoformat() if utc_value is not None else None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _summary_from_payload(payload: dict[str, object] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    summary = payload.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    final_output = payload.get("final_output")
    if isinstance(final_output, str) and final_output:
        return final_output
    return None
