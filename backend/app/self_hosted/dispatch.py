from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.admin.updates.service import maintenance_enabled
from backend.app.runs.models import AgentRun
from backend.app.runs.service import RunStateService
from backend.app.runs.status import RunStatus
from backend.app.self_hosted.dispatch_support import (
    SelfHostedClaimLockRepository,
    SelfHostedRunReservationService,
    SelfHostedWorkerCapacityService,
    SelfHostedWorkerEligibilityService,
)
from backend.app.self_hosted.events import SelfHostedEventRecorder
from backend.app.self_hosted.jobs import SelfHostedJobFinalizer
from backend.app.self_hosted.models import (
    SelfHostedJobClaim,
    SelfHostedMcpJob,
)
from backend.app.self_hosted.policy_gate import SelfHostedPolicyGate
from backend.app.self_hosted.types import AuthenticatedWorker
from backend.app.tasks.models import Task
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus


class SelfHostedDispatchService:
    def __init__(
        self,
        session: Session,
        events: SelfHostedEventRecorder,
        jobs: SelfHostedJobFinalizer,
    ) -> None:
        self._session = session
        self._events = events
        self._jobs = jobs
        self._eligibility = SelfHostedWorkerEligibilityService(session)
        self._capacity = SelfHostedWorkerCapacityService(session)
        self._locks = SelfHostedClaimLockRepository(session, jobs)
        self._reservations = SelfHostedRunReservationService(session)
        self._policy_gate = SelfHostedPolicyGate(session)

    def poll_job(self, auth: AuthenticatedWorker) -> AgentRun | None:
        self._policy_gate.require_enabled()
        self._eligibility.require_accepting_jobs(auth)
        statement = (
            select(AgentRun)
            .where(
                AgentRun.workspace_id == auth.worker.workspace_id,
                AgentRun.status == RunStatus.QUEUED.value,
                AgentRun.runtime_id == auth.runtime.id,
            )
            .order_by(AgentRun.created_at.asc())
        )
        for run in self._session.scalars(statement).all():
            if self._eligibility.can_accept_run(auth, run) and self._capacity.allows_run_job(auth):
                return run
        return None

    def claim_job(self, auth: AuthenticatedWorker, agent_run_id: UUID) -> SelfHostedJobClaim:
        if maintenance_enabled(self._session):
            raise ValueError("Platform is in maintenance; new claims are paused")
        self._policy_gate.require_enabled()
        auth = self._locks.locked_auth(auth)
        self._eligibility.require_accepting_jobs(auth)
        run = self._locks.locked_agent_run(agent_run_id)
        if (
            run is None
            or run.workspace_id != auth.worker.workspace_id
            or run.runtime_id != auth.runtime.id
        ):
            raise ValueError("Agent run not available for this worker")
        self._eligibility.require_run_authorized(run, full=True)
        self._eligibility.require_verified_isolation(auth, run)
        if not self._eligibility.runtime_space_allowed(auth, run):
            raise ValueError("Agent run runtime space is not allowed for this worker")
        policy_decision = self._eligibility.job_policy_decision(auth, run)
        if not policy_decision.allowed:
            raise ValueError(policy_decision.reason or "Agent run is not compatible with worker")
        existing_claim = self._jobs.job_claim_for_run(run)
        if existing_claim is not None:
            if existing_claim.worker_id == auth.worker.id and existing_claim.status == "claimed":
                return existing_claim
            raise ValueError("Agent run is already claimed")
        if not self._capacity.allows_run_job(auth):
            raise ValueError("Self-hosted worker has reached max concurrent jobs")
        if run.status != RunStatus.QUEUED.value:
            raise ValueError("Agent run is not queued")
        now = datetime.now(UTC)
        RunStateService().transition(run, RunStatus.RUNNING, started_at=now)
        self._session.flush([run])
        self._reservations.ensure_job_slot(auth, run)
        if run.task_id is not None:
            task = self._session.get(Task, run.task_id)
            if task is not None:
                TaskStateService().transition(task, TaskStatus.RUNNING)
        claim = SelfHostedJobClaim(
            workspace_id=run.workspace_id,
            worker_id=auth.worker.id,
            agent_run_id=run.id,
            claimed_at=now,
        )
        self._session.add(claim)
        self._events.append_run_event(run, "self_hosted.claimed", str(auth.worker.id), {})
        self._events.append_runtime_space_event(
            auth.runtime,
            "self_hosted.job_claimed",
            str(run.id),
            {"agent_run_id": str(run.id)},
        )
        self._session.commit()
        self._session.refresh(claim)
        return claim

    def poll_mcp_job(self, auth: AuthenticatedWorker) -> SelfHostedMcpJob | None:
        self._policy_gate.require_enabled()
        self._eligibility.require_accepting_jobs(auth)
        claimed_job = self._session.scalar(
            select(SelfHostedMcpJob)
            .where(
                SelfHostedMcpJob.workspace_id == auth.worker.workspace_id,
                SelfHostedMcpJob.workspace_runtime_id == auth.runtime.id,
                SelfHostedMcpJob.worker_id == auth.worker.id,
                SelfHostedMcpJob.status == "claimed",
            )
            .order_by(SelfHostedMcpJob.claimed_at.asc())
            .limit(1)
        )
        if claimed_job is not None:
            return claimed_job
        if not self._capacity.allows_mcp_job(auth):
            return None
        jobs = self._session.scalars(
            select(SelfHostedMcpJob)
            .where(
                SelfHostedMcpJob.workspace_id == auth.worker.workspace_id,
                SelfHostedMcpJob.workspace_runtime_id == auth.runtime.id,
                SelfHostedMcpJob.status == "queued",
            )
            .order_by(SelfHostedMcpJob.created_at.asc())
            .limit(50)
        )
        return next((job for job in jobs if self._eligibility.can_accept_mcp_job(auth, job)), None)

    def claim_mcp_job(self, auth: AuthenticatedWorker, mcp_job_id: UUID) -> SelfHostedMcpJob:
        if maintenance_enabled(self._session):
            raise ValueError("Platform is in maintenance; new claims are paused")
        self._policy_gate.require_enabled()
        auth = self._locks.locked_auth(auth)
        self._eligibility.require_accepting_jobs(auth)
        job = self._locks.locked_mcp_job(auth, mcp_job_id)
        if job is None:
            raise ValueError("Self-hosted MCP job not found")
        run = self._session.get(AgentRun, job.agent_run_id)
        if run is None or run.workspace_id != auth.worker.workspace_id:
            raise ValueError("Self-hosted MCP job run is unavailable")
        self._eligibility.require_verified_isolation(auth, run)
        if job.status == "claimed" and job.worker_id == auth.worker.id:
            return job
        if not self._capacity.allows_mcp_job(auth):
            raise ValueError("Self-hosted worker has reached max concurrent MCP jobs")
        if not self._eligibility.can_accept_mcp_job(auth, job):
            raise ValueError("Self-hosted MCP job is not compatible with worker")
        if job.status != "queued":
            raise ValueError("Self-hosted MCP job is not queued")
        now = datetime.now(UTC)
        claimed_id = self._session.scalar(
            update(SelfHostedMcpJob)
            .where(
                SelfHostedMcpJob.id == job.id,
                SelfHostedMcpJob.workspace_id == auth.worker.workspace_id,
                SelfHostedMcpJob.workspace_runtime_id == auth.runtime.id,
                SelfHostedMcpJob.status == "queued",
            )
            .values(status="claimed", worker_id=auth.worker.id, claimed_at=now)
            .returning(SelfHostedMcpJob.id)
        )
        if claimed_id is None:
            self._session.refresh(job)
            if job.status == "claimed" and job.worker_id == auth.worker.id:
                return job
            raise ValueError("Self-hosted MCP job is not queued")
        self._session.refresh(job)
        run = self._session.get(AgentRun, job.agent_run_id)
        if run is not None:
            self._events.append_run_event(
                run,
                "self_hosted.mcp_job_claimed",
                job.tool_name,
                {"mcp_job_id": str(job.id), "worker_id": str(auth.worker.id)},
            )
        self._session.commit()
        self._session.refresh(job)
        return job
