from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.app.core.typing import string_list
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.runtime_spaces.reservation_attachment import (
    RuntimeSpaceReservationAttachmentService,
)
from backend.app.runtime_spaces.reservation_capacity import RuntimeSpaceCapacityReservationService
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.jobs import SelfHostedJobFinalizer
from backend.app.self_hosted.models import (
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)
from backend.app.self_hosted.policy import evaluate_worker_job_policy
from backend.app.self_hosted.types import AuthenticatedWorker
from backend.app.workspaces.quotas import WorkspaceQuotaService


class SelfHostedWorkerEligibilityService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def require_accepting_jobs(self, auth: AuthenticatedWorker) -> None:
        if auth.worker.status in {"revoked", "quarantined", "offline", "degraded"}:
            raise ValueError(f"Self-hosted worker is {auth.worker.status}")
        if auth.runtime.status in {"revoked", "quarantined", "disabled"}:
            raise ValueError(f"Self-hosted runtime is {auth.runtime.status}")
        if auth.runtime.connection_status == "degraded":
            raise ValueError("Self-hosted runtime is degraded")

    def can_accept_run(self, auth: AuthenticatedWorker, run: AgentRun) -> bool:
        if not self.runtime_space_allowed(auth, run):
            return False
        return self.job_policy_decision(auth, run).allowed

    def can_accept_mcp_job(self, auth: AuthenticatedWorker, job: SelfHostedMcpJob) -> bool:
        allowed_tools = string_list(auth.worker.capabilities.get("allowed_tools"))
        return not allowed_tools or job.tool_name in allowed_tools

    def runtime_space_allowed(self, auth: AuthenticatedWorker, run: AgentRun) -> bool:
        if run.runtime_space_id is None:
            return True
        runtime_space_id = str(run.runtime_space_id)
        if auth.runtime.runtime_space_id is not None and runtime_space_id == str(
            auth.runtime.runtime_space_id
        ):
            return True
        allowed_ids = string_list(auth.worker.capabilities.get("allowed_runtime_space_ids"))
        return runtime_space_id in allowed_ids

    def job_policy_decision(self, auth: AuthenticatedWorker, run: AgentRun):
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


class SelfHostedWorkerCapacityService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def allows_run_job(self, auth: AuthenticatedWorker) -> bool:
        max_concurrent_jobs = positive_int(auth.worker.capabilities.get("max_concurrent_jobs"))
        if max_concurrent_jobs is None:
            return True
        return self.active_run_claims(auth) < max_concurrent_jobs

    def allows_mcp_job(self, auth: AuthenticatedWorker) -> bool:
        max_concurrent_jobs = positive_int(auth.worker.capabilities.get("max_concurrent_mcp_jobs"))
        if max_concurrent_jobs is None:
            max_concurrent_jobs = positive_int(auth.worker.capabilities.get("max_concurrent_jobs"))
        if max_concurrent_jobs is None:
            return True
        return self.active_mcp_jobs(auth) < max_concurrent_jobs

    def active_run_claims(self, auth: AuthenticatedWorker) -> int:
        running_claims = self._session.scalar(
            select(func.count(SelfHostedJobClaim.id)).where(
                SelfHostedJobClaim.workspace_id == auth.worker.workspace_id,
                SelfHostedJobClaim.worker_id == auth.worker.id,
                SelfHostedJobClaim.status == "claimed",
            )
        )
        return int(running_claims or 0)

    def active_mcp_jobs(self, auth: AuthenticatedWorker) -> int:
        running_jobs = self._session.scalar(
            select(func.count(SelfHostedMcpJob.id)).where(
                SelfHostedMcpJob.workspace_id == auth.worker.workspace_id,
                SelfHostedMcpJob.worker_id == auth.worker.id,
                SelfHostedMcpJob.status == "claimed",
            )
        )
        return int(running_jobs or 0)


class SelfHostedClaimLockRepository:
    def __init__(self, session: Session, jobs: SelfHostedJobFinalizer) -> None:
        self._session = session
        self._jobs = jobs

    def locked_auth(self, auth: AuthenticatedWorker) -> AuthenticatedWorker:
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
        self.lock_sqlite_worker_capacity_row(worker)
        return AuthenticatedWorker(worker=worker, runtime=runtime, credential=auth.credential)

    def lock_sqlite_worker_capacity_row(self, worker: SelfHostedWorker) -> None:
        if self._session.get_bind().dialect.name != "sqlite":
            return
        self._session.execute(
            update(SelfHostedWorker)
            .where(SelfHostedWorker.id == worker.id)
            .values(status=SelfHostedWorker.status)
        )
        self._session.expire(worker)

    def locked_agent_run(self, agent_run_id: UUID) -> AgentRun | None:
        return self._session.scalar(
            select(AgentRun)
            .where(AgentRun.id == agent_run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def locked_mcp_job(
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

    def locked_job_claim_for_run(self, run: AgentRun) -> SelfHostedJobClaim | None:
        return self._session.scalar(
            select(SelfHostedJobClaim)
            .where(
                SelfHostedJobClaim.workspace_id == run.workspace_id,
                SelfHostedJobClaim.agent_run_id == run.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )


class SelfHostedRunReservationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def ensure_job_slot(self, auth: AuthenticatedWorker, run: AgentRun) -> None:
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

        runtime_space_attachment = RuntimeSpaceReservationAttachmentService(self._session)
        runtime_space_usage = runtime_space_attachment.active_reservation_usage_for_run(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
        )
        runtime_space_reservation = None
        if runtime_space_usage.get("self_hosted_jobs", 0) <= 0:
            runtime_space_result = RuntimeSpaceCapacityReservationService(
                self._session
            ).reserve_run_capacity(
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
            runtime_space_attachment.attach_reservation_to_run(runtime_space_reservation, run.id)


def positive_int(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None
