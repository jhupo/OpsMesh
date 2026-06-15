from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.app.core.typing import string_list
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.runtime_spaces.service import RuntimeSpaceService
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.events import SelfHostedEventRecorder
from backend.app.self_hosted.jobs import SelfHostedJobFinalizer
from backend.app.self_hosted.models import (
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)
from backend.app.self_hosted.policy import evaluate_worker_job_policy
from backend.app.self_hosted.types import AuthenticatedWorker
from backend.app.tasks.models import Task
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus
from backend.app.workspaces.quotas import WorkspaceQuotaService


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

    def poll_job(self, auth: AuthenticatedWorker) -> AgentRun | None:
        self._require_worker_accepting_jobs(auth)
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
            if self._worker_can_accept_run(auth, run) and self._worker_capacity_allows(auth):
                return run
        return None

    def claim_job(self, auth: AuthenticatedWorker, agent_run_id: UUID) -> SelfHostedJobClaim:
        auth = self._locked_auth_for_claim(auth)
        self._require_worker_accepting_jobs(auth)
        run = self._locked_agent_run(agent_run_id)
        if (
            run is None
            or run.workspace_id != auth.worker.workspace_id
            or run.runtime_id != auth.runtime.id
        ):
            raise ValueError("Agent run not available for this worker")
        if not self._runtime_space_allowed(auth, run):
            raise ValueError("Agent run runtime space is not allowed for this worker")
        policy_decision = self._worker_job_policy_decision(auth, run)
        if not policy_decision.allowed:
            raise ValueError(policy_decision.reason or "Agent run is not compatible with worker")
        existing_claim = self._jobs.job_claim_for_run(run)
        if existing_claim is not None:
            if existing_claim.worker_id == auth.worker.id and existing_claim.status == "claimed":
                return existing_claim
            raise ValueError("Agent run is already claimed")
        if not self._worker_capacity_allows(auth):
            raise ValueError("Self-hosted worker has reached max concurrent jobs")
        if run.status != RunStatus.QUEUED.value:
            raise ValueError("Agent run is not queued")
        now = datetime.now(UTC)
        claimed = self._session.execute(
            update(AgentRun)
            .where(
                AgentRun.id == run.id,
                AgentRun.workspace_id == run.workspace_id,
                AgentRun.runtime_id == auth.runtime.id,
                AgentRun.status == RunStatus.QUEUED.value,
            )
            .values(status=RunStatus.RUNNING.value, started_at=now)
        )
        if claimed.rowcount != 1:
            self._session.refresh(run)
            existing_claim = self._locked_job_claim_for_run(run)
            if existing_claim is not None:
                if (
                    existing_claim.worker_id == auth.worker.id
                    and existing_claim.status == "claimed"
                ):
                    return existing_claim
                raise ValueError("Agent run is already claimed")
            raise ValueError("Agent run is not queued")
        self._session.refresh(run)
        self._ensure_self_hosted_job_slot(auth, run)
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
        self._require_worker_accepting_jobs(auth)
        if not self._worker_mcp_capacity_allows(auth):
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
        return next((job for job in jobs if self._worker_can_accept_mcp_job(auth, job)), None)

    def claim_mcp_job(self, auth: AuthenticatedWorker, mcp_job_id: UUID) -> SelfHostedMcpJob:
        auth = self._locked_auth_for_claim(auth)
        self._require_worker_accepting_jobs(auth)
        job = self._locked_mcp_job(auth, mcp_job_id)
        if job is None:
            raise ValueError("Self-hosted MCP job not found")
        if job.status == "claimed" and job.worker_id == auth.worker.id:
            return job
        if not self._worker_mcp_capacity_allows(auth):
            raise ValueError("Self-hosted worker has reached max concurrent MCP jobs")
        if not self._worker_can_accept_mcp_job(auth, job):
            raise ValueError("Self-hosted MCP job is not compatible with worker")
        if job.status != "queued":
            raise ValueError("Self-hosted MCP job is not queued")
        now = datetime.now(UTC)
        claimed = self._session.execute(
            update(SelfHostedMcpJob)
            .where(
                SelfHostedMcpJob.id == job.id,
                SelfHostedMcpJob.workspace_id == auth.worker.workspace_id,
                SelfHostedMcpJob.workspace_runtime_id == auth.runtime.id,
                SelfHostedMcpJob.status == "queued",
            )
            .values(status="claimed", worker_id=auth.worker.id, claimed_at=now)
        )
        if claimed.rowcount != 1:
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

    def _runtime_space_allowed(self, auth: AuthenticatedWorker, run: AgentRun) -> bool:
        if run.runtime_space_id is None:
            return True
        runtime_space_id = str(run.runtime_space_id)
        if auth.runtime.runtime_space_id is not None and runtime_space_id == str(
            auth.runtime.runtime_space_id
        ):
            return True
        allowed_ids = string_list(auth.worker.capabilities.get("allowed_runtime_space_ids"))
        return runtime_space_id in allowed_ids

    def _worker_can_accept_run(self, auth: AuthenticatedWorker, run: AgentRun) -> bool:
        if not self._runtime_space_allowed(auth, run):
            return False
        return self._worker_job_policy_decision(auth, run).allowed

    def _worker_can_accept_mcp_job(
        self,
        auth: AuthenticatedWorker,
        job: SelfHostedMcpJob,
    ) -> bool:
        allowed_tools = string_list(auth.worker.capabilities.get("allowed_tools"))
        return not allowed_tools or job.tool_name in allowed_tools

    def _worker_job_policy_decision(self, auth: AuthenticatedWorker, run: AgentRun):
        runtime_space = (
            self._session.get(RuntimeSpace, run.runtime_space_id)
            if run.runtime_space_id is not None
            else None
        )
        return evaluate_worker_job_policy(
            worker_capabilities=auth.worker.capabilities,
            run=run,
            runtime_space=runtime_space,
        )

    def _require_worker_accepting_jobs(self, auth: AuthenticatedWorker) -> None:
        if auth.worker.status in {"revoked", "quarantined", "offline", "degraded"}:
            raise ValueError(f"Self-hosted worker is {auth.worker.status}")
        if auth.runtime.status in {"revoked", "quarantined", "disabled"}:
            raise ValueError(f"Self-hosted runtime is {auth.runtime.status}")
        if auth.runtime.connection_status == "degraded":
            raise ValueError("Self-hosted runtime is degraded")

    def _locked_auth_for_claim(self, auth: AuthenticatedWorker) -> AuthenticatedWorker:
        worker = self._session.scalar(
            select(SelfHostedWorker)
            .where(
                SelfHostedWorker.id == auth.worker.id,
                SelfHostedWorker.workspace_id == auth.worker.workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        runtime = self._session.scalar(
            select(WorkspaceRuntime)
            .where(
                WorkspaceRuntime.id == auth.runtime.id,
                WorkspaceRuntime.workspace_id == auth.worker.workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if worker is None or runtime is None:
            raise ValueError("Runtime credential is orphaned")
        self._lock_sqlite_worker_capacity_row(worker)
        return AuthenticatedWorker(worker=worker, runtime=runtime, credential=auth.credential)

    def _lock_sqlite_worker_capacity_row(self, worker: SelfHostedWorker) -> None:
        if self._session.get_bind().dialect.name != "sqlite":
            return
        self._session.execute(
            update(SelfHostedWorker)
            .where(SelfHostedWorker.id == worker.id)
            .values(status=SelfHostedWorker.status)
        )
        self._session.expire(worker)

    def _locked_agent_run(self, agent_run_id: UUID) -> AgentRun | None:
        return self._session.scalar(
            select(AgentRun)
            .where(AgentRun.id == agent_run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _locked_mcp_job(
        self,
        auth: AuthenticatedWorker,
        mcp_job_id: UUID,
    ) -> SelfHostedMcpJob | None:
        return self._session.scalar(
            select(SelfHostedMcpJob)
            .where(
                SelfHostedMcpJob.id == mcp_job_id,
                SelfHostedMcpJob.workspace_id == auth.worker.workspace_id,
                SelfHostedMcpJob.workspace_runtime_id == auth.runtime.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _locked_job_claim_for_run(self, run: AgentRun) -> SelfHostedJobClaim | None:
        return self._session.scalar(
            select(SelfHostedJobClaim)
            .where(
                SelfHostedJobClaim.workspace_id == run.workspace_id,
                SelfHostedJobClaim.agent_run_id == run.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _worker_capacity_allows(self, auth: AuthenticatedWorker) -> bool:
        max_concurrent_jobs = _positive_int(auth.worker.capabilities.get("max_concurrent_jobs"))
        if max_concurrent_jobs is None:
            return True
        running_claims = self._session.scalar(
            select(func.count(SelfHostedJobClaim.id)).where(
                SelfHostedJobClaim.workspace_id == auth.worker.workspace_id,
                SelfHostedJobClaim.worker_id == auth.worker.id,
                SelfHostedJobClaim.status == "claimed",
            )
        )
        return int(running_claims or 0) < max_concurrent_jobs

    def _worker_mcp_capacity_allows(self, auth: AuthenticatedWorker) -> bool:
        max_concurrent_jobs = _positive_int(auth.worker.capabilities.get("max_concurrent_mcp_jobs"))
        if max_concurrent_jobs is None:
            max_concurrent_jobs = _positive_int(auth.worker.capabilities.get("max_concurrent_jobs"))
        if max_concurrent_jobs is None:
            return True
        running_jobs = self._session.scalar(
            select(func.count(SelfHostedMcpJob.id)).where(
                SelfHostedMcpJob.workspace_id == auth.worker.workspace_id,
                SelfHostedMcpJob.worker_id == auth.worker.id,
                SelfHostedMcpJob.status == "claimed",
            )
        )
        return int(running_jobs or 0) < max_concurrent_jobs

    def _ensure_self_hosted_job_slot(
        self,
        auth: AuthenticatedWorker,
        run: AgentRun,
    ) -> None:
        workspace_quota = WorkspaceQuotaService(self._session)
        workspace_usage = workspace_quota.active_reservation_usage_for_run(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
        )
        workspace_reservation = None
        if workspace_usage.get("self_hosted_jobs", 0) <= 0:
            workspace_result = workspace_quota.reserve(
                workspace_id=run.workspace_id,
                task_id=run.task_id,
                task_step_id=run.task_step_id,
                reservation_key=f"self_hosted_job:{run.id}:workspace",
                resource_usage={"self_hosted_jobs": 1},
                ensure_active_run=False,
            )
            if workspace_result.reservation is None:
                raise ValueError(workspace_result.blocked_reason or "workspace_quota_exceeded")
            workspace_reservation = workspace_result.reservation

        runtime_space_id = run.runtime_space_id or auth.runtime.runtime_space_id
        if runtime_space_id is None:
            if workspace_reservation is not None:
                workspace_quota.attach_reservation_to_run(workspace_reservation, run.id)
            return

        runtime_space_service = RuntimeSpaceService(self._session)
        runtime_space_usage = runtime_space_service.active_reservation_usage_for_run(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
        )
        runtime_space_reservation = None
        if runtime_space_usage.get("self_hosted_jobs", 0) <= 0:
            runtime_space_result = runtime_space_service.reserve_run_capacity(
                workspace_id=run.workspace_id,
                runtime_space_id=runtime_space_id,
                task_id=run.task_id,
                task_step_id=run.task_step_id,
                reservation_key=f"self_hosted_job:{run.id}:runtime_space",
                resource_usage={"self_hosted_jobs": 1},
            )
            if runtime_space_result.reservation is None:
                if workspace_reservation is not None:
                    workspace_quota.release_reservation(
                        workspace_reservation,
                        released_at=datetime.now(UTC),
                    )
                raise ValueError(
                    runtime_space_result.blocked_reason or "runtime_space_quota_exceeded"
                )
            runtime_space_reservation = runtime_space_result.reservation

        if workspace_reservation is not None:
            workspace_quota.attach_reservation_to_run(workspace_reservation, run.id)
        if runtime_space_reservation is not None:
            runtime_space_service.attach_reservation_to_run(runtime_space_reservation, run.id)


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None
