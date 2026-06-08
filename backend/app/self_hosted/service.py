from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.app.admin.policies import PlatformPolicyService
from backend.app.api.schemas.self_hosted import (
    ArtifactUploadRequest,
    EnrollmentTokenCreateRequest,
    JobCompleteRequest,
    LocalFileReferenceRequest,
    McpJobCompleteRequest,
    ProgressEventRequest,
    RuntimeRegistrationRequest,
    WorkerHeartbeatRequest,
)
from backend.app.capabilities.models import McpServer
from backend.app.core.config import Settings
from backend.app.files.security import safe_filename, validate_storage_key
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceEvent
from backend.app.runtime_spaces.service import RuntimeSpaceService
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime
from backend.app.self_hosted.models import (
    LocalFileReference,
    RuntimeCredential,
    RuntimeEnrollmentToken,
    SelfHostedArtifactUpload,
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)
from backend.app.self_hosted.policy import evaluate_worker_job_policy
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus
from backend.app.workspaces.models import Workspace
from backend.app.workspaces.quotas import WorkspaceQuotaService


@dataclass(frozen=True)
class CreatedEnrollmentToken:
    record: RuntimeEnrollmentToken
    token: str


@dataclass(frozen=True)
class RegisteredRuntime:
    workspace_id: UUID
    workspace_runtime_id: UUID
    worker_id: UUID
    credential_token: str


@dataclass(frozen=True)
class AuthenticatedWorker:
    worker: SelfHostedWorker
    runtime: WorkspaceRuntime
    credential: RuntimeCredential


@dataclass(frozen=True)
class WorkerTrustCleanupResult:
    degraded: int = 0
    quarantined: int = 0
    expired_job_claims: int = 0
    expired_mcp_jobs: int = 0


@dataclass(frozen=True)
class WorkerControlResult:
    worker: SelfHostedWorker
    runtime: WorkspaceRuntime
    action: str
    affected_claims: int
    affected_runs: int


@dataclass(frozen=True)
class WorkerTrustSnapshot:
    worker: SelfHostedWorker
    runtime: WorkspaceRuntime
    credential: RuntimeCredential | None
    trust_state: str
    policy_summary: dict[str, object]
    policy_diagnostics: list[dict[str, object]]


class SelfHostedRuntimeService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def create_enrollment_token(
        self,
        workspace_id: UUID,
        user_id: UUID,
        data: EnrollmentTokenCreateRequest,
    ) -> CreatedEnrollmentToken:
        self._require_self_hosted_enabled()
        token = f"ccrt_{token_urlsafe(32)}"
        record = RuntimeEnrollmentToken(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            token_hash=self._hash(token),
            name=data.name,
            expires_at=data.expires_at,
        )
        self._session.add(record)
        self._session.commit()
        self._session.refresh(record)
        return CreatedEnrollmentToken(record=record, token=token)

    def register_runtime(self, data: RuntimeRegistrationRequest) -> RegisteredRuntime:
        self._require_self_hosted_enabled()
        token = self._consume_enrollment_token(data.enrollment_token)
        now = datetime.now(UTC)
        capabilities = self._validated_capabilities(
            token.workspace_id,
            data.capabilities,
        )
        runtime_space_id = self._registration_runtime_space_id(token.workspace_id, capabilities)
        runtime = WorkspaceRuntime(
            workspace_id=token.workspace_id,
            runtime_space_id=runtime_space_id,
            runtime_provider="self_hosted",
            runtime_type="self_hosted",
            name=data.name,
            status="active",
            connection_status="online",
            capabilities=capabilities,
            last_heartbeat_at=now,
        )
        self._session.add(runtime)
        self._session.flush()
        credential_token = f"ccwc_{token_urlsafe(32)}"
        credential = RuntimeCredential(
            workspace_id=token.workspace_id,
            workspace_runtime_id=runtime.id,
            token_hash=self._hash(credential_token),
            last_used_at=now,
        )
        worker = SelfHostedWorker(
            workspace_id=token.workspace_id,
            workspace_runtime_id=runtime.id,
            name=data.name,
            machine_id=data.machine_id,
            version=data.version,
            capabilities=capabilities,
            last_heartbeat_at=now,
        )
        token.status = "used"
        token.used_at = now
        self._session.add_all([credential, worker])
        self._append_runtime_event(runtime, "self_hosted.registered", data.machine_id)
        self._append_runtime_space_event(runtime, "self_hosted.registered", data.machine_id)
        self._session.commit()
        self._session.refresh(runtime)
        self._session.refresh(worker)
        return RegisteredRuntime(
            workspace_id=token.workspace_id,
            workspace_runtime_id=runtime.id,
            worker_id=worker.id,
            credential_token=credential_token,
        )

    def authenticate_worker(self, credential_token: str) -> AuthenticatedWorker:
        credential = self._session.scalar(
            select(RuntimeCredential).where(
                RuntimeCredential.token_hash == self._hash(credential_token),
                RuntimeCredential.status == "active",
            )
        )
        if credential is None:
            raise ValueError("Invalid runtime credential")
        runtime = self._session.get(WorkspaceRuntime, credential.workspace_runtime_id)
        worker = self._session.scalar(
            select(SelfHostedWorker).where(
                SelfHostedWorker.workspace_runtime_id == credential.workspace_runtime_id
            )
        )
        if runtime is None or worker is None:
            raise ValueError("Runtime credential is orphaned")
        credential.last_used_at = datetime.now(UTC)
        return AuthenticatedWorker(worker=worker, runtime=runtime, credential=credential)

    def heartbeat(
        self,
        auth: AuthenticatedWorker,
        data: WorkerHeartbeatRequest,
    ) -> SelfHostedWorker:
        now = datetime.now(UTC)
        if auth.worker.status == "quarantined" or auth.runtime.status == "quarantined":
            raise ValueError("Self-hosted worker is quarantined")
        capabilities = self._validated_capabilities(
            auth.worker.workspace_id,
            data.capabilities or auth.worker.capabilities,
            bound_runtime_space_id=auth.runtime.runtime_space_id,
        )
        auth.worker.status = data.status
        auth.worker.capabilities = capabilities
        auth.worker.last_heartbeat_at = now
        auth.runtime.connection_status = "online" if data.status == "online" else data.status
        auth.runtime.capabilities = capabilities
        auth.runtime.last_heartbeat_at = now
        self._append_runtime_event(auth.runtime, "self_hosted.heartbeat", data.status)
        self._append_runtime_space_event(auth.runtime, "self_hosted.heartbeat", data.status)
        self._session.commit()
        self._session.refresh(auth.worker)
        return auth.worker

    def poll_job(self, auth: AuthenticatedWorker) -> AgentRun | None:
        self._require_self_hosted_enabled()
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
        self._require_self_hosted_enabled()
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
        existing_claim = self._job_claim_for_run(run)
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
        self._append_run_event(run, "self_hosted.claimed", str(auth.worker.id), {})
        self._append_runtime_space_event(
            auth.runtime,
            "self_hosted.job_claimed",
            str(run.id),
            {"agent_run_id": str(run.id)},
        )
        self._session.commit()
        self._session.refresh(claim)
        return claim

    def complete_job(
        self,
        auth: AuthenticatedWorker,
        agent_run_id: UUID,
        data: JobCompleteRequest,
    ) -> SelfHostedJobClaim:
        run = self._require_worker_run(auth, agent_run_id)
        claim = self._job_claim_for_run(run)
        if claim is None or claim.worker_id != auth.worker.id:
            raise ValueError("Self-hosted job is not claimed by this worker")
        if claim.status in {"completed", "failed"}:
            if claim.status != data.status:
                raise ValueError("Self-hosted job was already completed with a different status")
            return claim
        if claim.status != "claimed":
            raise ValueError("Self-hosted job is not active")
        now = datetime.now(UTC)
        claim.status = data.status
        claim.completed_at = now
        run.completed_at = now
        if data.status == "completed":
            run.status = RunStatus.COMPLETED.value
            run.output = data.output or {}
            self._mark_task_completed_from_self_hosted_run(run, data.output)
        else:
            run.status = RunStatus.FAILED.value
            run.error = data.error or {
                "code": "self_hosted_job_failed",
                "message": "Self-hosted job failed",
            }
            self._mark_task_failed_from_self_hosted_run(run)
        self._release_run_reservations(run, released_at=now)
        self._append_run_event(
            run,
            f"self_hosted.job_{data.status}",
            f"Self-hosted job {data.status}",
            {
                "claim_id": str(claim.id),
                "worker_id": str(auth.worker.id),
                "runtime_id": str(auth.runtime.id),
            },
        )
        self._append_runtime_space_event(
            auth.runtime,
            f"self_hosted.job_{data.status}",
            str(run.id),
            {"agent_run_id": str(run.id), "claim_id": str(claim.id)},
        )
        self._session.commit()
        self._session.refresh(claim)
        return claim

    def create_mcp_job(
        self,
        *,
        workspace_id: UUID,
        runtime_id: UUID,
        agent_run_id: UUID,
        mcp_server_id: UUID,
        tool_name: str,
        request_payload: dict[str, object],
    ) -> SelfHostedMcpJob:
        run = self._session.get(AgentRun, agent_run_id)
        server = self._session.get(McpServer, mcp_server_id)
        runtime = self._session.get(WorkspaceRuntime, runtime_id)
        if (
            run is None
            or server is None
            or runtime is None
            or run.workspace_id != workspace_id
            or server.workspace_id != workspace_id
            or runtime.workspace_id != workspace_id
            or run.runtime_id != runtime_id
        ):
            raise ValueError("Self-hosted MCP job scope is invalid")
        job = SelfHostedMcpJob(
            workspace_id=workspace_id,
            workspace_runtime_id=runtime_id,
            agent_run_id=agent_run_id,
            mcp_server_id=mcp_server_id,
            tool_name=tool_name,
            request_payload=request_payload,
        )
        self._session.add(job)
        self._session.flush()
        self._append_run_event(
            run,
            "self_hosted.mcp_job_queued",
            tool_name,
            {"mcp_job_id": str(job.id), "mcp_server_id": str(mcp_server_id)},
        )
        self._session.commit()
        self._session.refresh(job)
        return job

    def poll_mcp_job(self, auth: AuthenticatedWorker) -> SelfHostedMcpJob | None:
        self._require_self_hosted_enabled()
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
        self._require_self_hosted_enabled()
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
            self._append_run_event(
                run,
                "self_hosted.mcp_job_claimed",
                job.tool_name,
                {"mcp_job_id": str(job.id), "worker_id": str(auth.worker.id)},
            )
        self._session.commit()
        self._session.refresh(job)
        return job

    def complete_mcp_job(
        self,
        auth: AuthenticatedWorker,
        mcp_job_id: UUID,
        data: McpJobCompleteRequest,
    ) -> SelfHostedMcpJob:
        job = self._require_mcp_job(auth, mcp_job_id)
        if job.status != "claimed" or job.worker_id != auth.worker.id:
            raise ValueError("Self-hosted MCP job is not claimed by this worker")
        job.status = data.status
        job.response_payload = data.response_payload
        job.error_payload = data.error_payload
        job.completed_at = datetime.now(UTC)
        run = self._session.get(AgentRun, job.agent_run_id)
        if run is not None:
            self._record_mcp_job_completion_for_run(run, job)
            self._append_run_event(
                run,
                f"self_hosted.mcp_job_{data.status}",
                job.tool_name,
                {
                    "mcp_job_id": str(job.id),
                    "mcp_server_id": str(job.mcp_server_id),
                    "response_present": data.response_payload is not None,
                    "error_present": data.error_payload is not None,
                },
            )
        self._session.commit()
        self._session.refresh(job)
        return job

    def upload_progress(self, auth: AuthenticatedWorker, data: ProgressEventRequest) -> RunEvent:
        run = self._require_worker_run(auth, data.agent_run_id)
        event = self._append_run_event(run, data.event_type, data.message, data.metadata)
        self._session.commit()
        self._session.refresh(event)
        return event

    def create_local_file_reference(
        self,
        auth: AuthenticatedWorker,
        data: LocalFileReferenceRequest,
    ) -> LocalFileReference:
        if data.task_id is not None:
            task = self._session.get(Task, data.task_id)
            if task is None or task.workspace_id != auth.worker.workspace_id:
                raise ValueError("Task not found")
        reference = LocalFileReference(
            workspace_id=auth.worker.workspace_id,
            workspace_runtime_id=auth.runtime.id,
            task_id=data.task_id,
            path=data.path,
            label=data.label,
            file_metadata={
                **data.metadata,
                "runtime_space_id": str(auth.runtime.runtime_space_id)
                if auth.runtime.runtime_space_id is not None
                else None,
                "workspace_runtime_id": str(auth.runtime.id),
                "worker_id": str(auth.worker.id),
            },
        )
        self._session.add(reference)
        self._session.commit()
        self._session.refresh(reference)
        return reference

    def register_artifact_upload(
        self,
        auth: AuthenticatedWorker,
        data: ArtifactUploadRequest,
    ) -> SelfHostedArtifactUpload:
        if data.agent_run_id is not None:
            self._require_worker_run(auth, data.agent_run_id)
        run = (
            self._session.get(AgentRun, data.agent_run_id)
            if data.agent_run_id is not None
            else None
        )
        step = (
            self._session.get(TaskStep, run.task_step_id)
            if run is not None and run.task_step_id is not None
            else None
        )
        max_artifact_bytes = _positive_int(auth.worker.capabilities.get("max_artifact_bytes"))
        artifact_size = _positive_int(data.metadata.get("size_bytes"))
        if (
            max_artifact_bytes is not None
            and artifact_size is not None
            and artifact_size > max_artifact_bytes
        ):
            raise ValueError("Artifact upload exceeds self-hosted worker policy")
        filename = safe_filename(data.filename, default="artifact.bin")
        storage_key = (
            validate_storage_key(
                data.storage_key,
                expected_prefix=f"workspaces/{auth.worker.workspace_id}/self-hosted",
            )
            if data.storage_key is not None
            else None
        )
        upload = SelfHostedArtifactUpload(
            workspace_id=auth.worker.workspace_id,
            worker_id=auth.worker.id,
            agent_run_id=data.agent_run_id,
            filename=filename,
            storage_key=storage_key,
            checksum_sha256=data.checksum_sha256,
            artifact_metadata={
                **data.metadata,
                "task_id": str(run.task_id) if run is not None and run.task_id else None,
                "task_step_id": str(step.id) if step is not None else None,
                "agent_profile_id": str(run.agent_profile_id)
                if run is not None and run.agent_profile_id
                else None,
                "work_package_id": step.work_package_id if step is not None else None,
                "runtime_space_id": str(auth.runtime.runtime_space_id)
                if auth.runtime.runtime_space_id is not None
                else None,
                "workspace_runtime_id": str(auth.runtime.id),
                "worker_id": str(auth.worker.id),
            },
        )
        self._session.add(upload)
        self._session.commit()
        self._session.refresh(upload)
        return upload

    def revoke_credential(
        self,
        workspace_id: UUID,
        credential_id: UUID,
        *,
        actor_user_id: UUID | None = None,
        reason: str = "",
    ) -> RuntimeCredential | None:
        credential = self._session.get(RuntimeCredential, credential_id)
        if credential is None or credential.workspace_id != workspace_id:
            return None
        now = datetime.now(UTC)
        credential.status = "revoked"
        credential.revoked_at = now
        runtime = self._session.get(WorkspaceRuntime, credential.workspace_runtime_id)
        worker = self._session.scalar(
            select(SelfHostedWorker).where(
                SelfHostedWorker.workspace_runtime_id == credential.workspace_runtime_id
            )
        )
        affected_claims = self._active_claims_for_worker(worker) if worker is not None else []
        affected_run_ids: list[str] = []
        for claim in affected_claims:
            claim.status = "revoked"
            claim.completed_at = now
            run = self._session.get(AgentRun, claim.agent_run_id)
            if run is None:
                continue
            affected_run_ids.append(str(run.id))
            if run.status in {
                RunStatus.QUEUED.value,
                RunStatus.RUNNING.value,
                RunStatus.WAITING_APPROVAL.value,
            }:
                run.status = RunStatus.FAILED.value
                run.completed_at = now
                run.error = {
                    "code": "runtime_credential_revoked",
                    "message": "Self-hosted runtime credential was revoked.",
                    "credential_id": str(credential.id),
                }
                self._append_run_event(
                    run,
                    "self_hosted.run_failed_by_revoke",
                    "Self-hosted runtime credential was revoked.",
                    {
                        "credential_id": str(credential.id),
                        "worker_id": str(worker.id) if worker else None,
                        "reason": reason,
                    },
                )
            self._release_run_reservations(run, released_at=now)
        if worker is not None:
            worker.status = "revoked"
        if runtime is not None:
            runtime.status = "revoked"
            runtime.connection_status = "offline"
            event_metadata = {
                "credential_id": str(credential.id),
                "worker_id": str(worker.id) if worker else None,
                "actor_user_id": str(actor_user_id) if actor_user_id is not None else None,
                "reason": reason,
                "affected_claim_ids": [str(claim.id) for claim in affected_claims],
                "affected_run_ids": affected_run_ids,
                "final_heartbeat": {
                    "worker_status": worker.status if worker is not None else None,
                    "worker_last_heartbeat_at": _dt_iso(worker.last_heartbeat_at)
                    if worker is not None
                    else None,
                    "runtime_status": runtime.status,
                    "runtime_connection_status": runtime.connection_status,
                    "runtime_last_heartbeat_at": _dt_iso(runtime.last_heartbeat_at),
                },
            }
            self._append_runtime_event(
                runtime,
                "self_hosted.credential_revoked",
                reason or str(credential.id),
                event_metadata,
            )
            self._append_runtime_space_event(
                runtime,
                "self_hosted.credential_revoked",
                str(credential.id),
                event_metadata,
            )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def control_worker(
        self,
        workspace_id: UUID,
        worker_id: UUID,
        *,
        action: str,
        actor_user_id: UUID | None = None,
        reason: str = "",
    ) -> WorkerControlResult | None:
        worker = self._session.get(SelfHostedWorker, worker_id)
        if worker is None or worker.workspace_id != workspace_id:
            return None
        runtime = self._session.get(WorkspaceRuntime, worker.workspace_runtime_id)
        if runtime is None or runtime.workspace_id != workspace_id:
            return None
        now = datetime.now(UTC)
        normalized_action = action.strip().lower()
        affected_claims = 0
        affected_runs = 0
        metadata = {
            "worker_id": str(worker.id),
            "actor_user_id": str(actor_user_id) if actor_user_id is not None else None,
            "reason": reason,
            "previous_worker_status": worker.status,
            "previous_runtime_status": runtime.status,
            "previous_connection_status": runtime.connection_status,
        }

        if normalized_action == "quarantine":
            worker.status = "quarantined"
            runtime.status = "quarantined"
            runtime.connection_status = "offline"
            affected_claims, affected_runs = self._close_active_claims_for_worker(
                worker,
                claim_status="quarantined",
                error_code="self_hosted_worker_quarantined",
                error_message="Self-hosted worker was quarantined by an operator.",
                event_type="self_hosted.run_failed_by_quarantine",
                event_message="Self-hosted worker was quarantined.",
                now=now,
                reason=reason,
            )
        elif normalized_action == "resume":
            if worker.status == "revoked" or runtime.status == "revoked":
                raise ValueError("Revoked self-hosted workers cannot be resumed")
            worker.status = "online"
            runtime.status = "active"
            runtime.connection_status = _connection_status_after_resume(
                worker.last_heartbeat_at,
                now,
            )
        elif normalized_action == "revoke":
            credential = self._latest_credential_for_runtime(runtime.id)
            if credential is not None and credential.status != "revoked":
                active_claims = self._active_claims_for_worker(worker)
                affected_claims = len(active_claims)
                affected_runs = len({claim.agent_run_id for claim in active_claims})
                revoked = self.revoke_credential(
                    workspace_id,
                    credential.id,
                    actor_user_id=actor_user_id,
                    reason=reason,
                )
                if revoked is None:
                    return None
                worker = self._session.get(SelfHostedWorker, worker_id) or worker
                runtime = self._session.get(WorkspaceRuntime, runtime.id) or runtime
                return WorkerControlResult(
                    worker=worker,
                    runtime=runtime,
                    action=normalized_action,
                    affected_claims=affected_claims,
                    affected_runs=affected_runs,
                )
            worker.status = "revoked"
            runtime.status = "revoked"
            runtime.connection_status = "offline"
            affected_claims, affected_runs = self._close_active_claims_for_worker(
                worker,
                claim_status="revoked",
                error_code="self_hosted_worker_revoked",
                error_message="Self-hosted worker was revoked by an operator.",
                event_type="self_hosted.run_failed_by_revoke",
                event_message="Self-hosted worker was revoked.",
                now=now,
                reason=reason,
            )
        else:
            raise ValueError("Unsupported self-hosted worker control action")

        metadata |= {
            "action": normalized_action,
            "worker_status": worker.status,
            "runtime_status": runtime.status,
            "connection_status": runtime.connection_status,
            "affected_claims": affected_claims,
            "affected_runs": affected_runs,
        }
        self._append_runtime_event(
            runtime,
            f"self_hosted.worker_{normalized_action}",
            reason or worker.machine_id,
            metadata,
        )
        self._append_runtime_space_event(
            runtime,
            f"self_hosted.worker_{normalized_action}",
            reason or worker.machine_id,
            metadata,
        )
        self._session.commit()
        self._session.refresh(worker)
        self._session.refresh(runtime)
        return WorkerControlResult(
            worker=worker,
            runtime=runtime,
            action=normalized_action,
            affected_claims=affected_claims,
            affected_runs=affected_runs,
        )

    def _require_self_hosted_enabled(self) -> None:
        policy = PlatformPolicyService(self._session).risky_execution_policy()
        if not policy.allow_self_hosted_runtimes:
            raise ValueError("Self-hosted runtimes are disabled by platform safety policy")

    def cleanup_stale_workers(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int = 600,
        quarantine_after_seconds: int | None = None,
        job_claim_stale_after_seconds: int = 900,
        mcp_job_stale_after_seconds: int = 900,
    ) -> WorkerTrustCleanupResult:
        now = datetime.now(UTC)
        degraded_cutoff = now - timedelta(seconds=stale_after_seconds)
        job_claim_cutoff = now - timedelta(seconds=job_claim_stale_after_seconds)
        mcp_job_cutoff = now - timedelta(seconds=mcp_job_stale_after_seconds)
        quarantine_after_seconds = quarantine_after_seconds or stale_after_seconds * 3
        quarantine_after_seconds = max(quarantine_after_seconds, stale_after_seconds)
        quarantine_cutoff = now - timedelta(seconds=quarantine_after_seconds)
        stale_workers = self._session.scalars(
            select(SelfHostedWorker).where(
                SelfHostedWorker.workspace_id == workspace_id,
                SelfHostedWorker.status.in_(["online", "degraded"]),
                SelfHostedWorker.last_heartbeat_at.is_not(None),
                SelfHostedWorker.last_heartbeat_at < degraded_cutoff,
            )
        ).all()
        degraded = 0
        quarantined = 0
        for worker in stale_workers:
            runtime = self._session.get(WorkspaceRuntime, worker.workspace_runtime_id)
            last_heartbeat_at = _as_utc(worker.last_heartbeat_at)
            if last_heartbeat_at is None:
                continue
            should_quarantine = last_heartbeat_at <= quarantine_cutoff
            if should_quarantine:
                if worker.status != "quarantined":
                    quarantined += 1
                worker.status = "quarantined"
                if runtime is not None:
                    runtime.status = "quarantined"
                    runtime.connection_status = "offline"
                    metadata = {
                        "worker_id": str(worker.id),
                        "last_heartbeat_at": last_heartbeat_at.isoformat(),
                        "stale_after_seconds": stale_after_seconds,
                        "quarantine_after_seconds": quarantine_after_seconds,
                        "quarantined_at": now.isoformat(),
                    }
                    self._append_runtime_event(
                        runtime,
                        "self_hosted.worker_quarantined",
                        worker.machine_id,
                        metadata,
                    )
                    self._append_runtime_space_event(
                        runtime,
                        "self_hosted.worker_quarantined",
                        worker.machine_id,
                        metadata,
                    )
                continue
            if worker.status != "degraded":
                degraded += 1
            worker.status = "degraded"
            if runtime is not None:
                runtime.connection_status = "degraded"
                metadata = {
                    "worker_id": str(worker.id),
                    "last_heartbeat_at": last_heartbeat_at.isoformat(),
                    "stale_after_seconds": stale_after_seconds,
                    "degraded_at": now.isoformat(),
                }
                self._append_runtime_event(
                    runtime,
                    "self_hosted.worker_degraded",
                    worker.machine_id,
                    metadata,
                )
                self._append_runtime_space_event(
                    runtime,
                    "self_hosted.worker_degraded",
                    worker.machine_id,
                    metadata,
                )
        expired_job_claims = self._expire_stale_job_claims(workspace_id, job_claim_cutoff, now)
        expired_mcp_jobs = self._expire_stale_mcp_jobs(workspace_id, mcp_job_cutoff, now)
        self._session.commit()
        return WorkerTrustCleanupResult(
            degraded=degraded,
            quarantined=quarantined,
            expired_job_claims=expired_job_claims,
            expired_mcp_jobs=expired_mcp_jobs,
        )

    def list_worker_trust(self, workspace_id: UUID) -> list[WorkerTrustSnapshot]:
        version_policy = self._workspace_self_hosted_version_policy(workspace_id)
        rows = self._session.scalars(
            select(SelfHostedWorker)
            .where(SelfHostedWorker.workspace_id == workspace_id)
            .order_by(SelfHostedWorker.updated_at.desc(), SelfHostedWorker.name)
        ).all()
        snapshots: list[WorkerTrustSnapshot] = []
        for worker in rows:
            runtime = self._session.get(WorkspaceRuntime, worker.workspace_runtime_id)
            if runtime is None or runtime.workspace_id != workspace_id:
                continue
            credential = self._session.scalar(
                select(RuntimeCredential)
                .where(RuntimeCredential.workspace_runtime_id == runtime.id)
                .order_by(RuntimeCredential.created_at.desc())
                .limit(1)
            )
            snapshots.append(
                WorkerTrustSnapshot(
                    worker=worker,
                    runtime=runtime,
                    credential=credential,
                    trust_state=_worker_trust_state(worker, runtime, credential),
                    policy_summary=_worker_policy_summary(worker.capabilities),
                    policy_diagnostics=_worker_policy_diagnostics(
                        worker,
                        runtime,
                        version_policy=version_policy,
                    ),
                )
            )
        return snapshots

    def connector_manifest(self, workspace_id: UUID) -> dict[str, object]:
        version_policy = self._workspace_self_hosted_version_policy(workspace_id)
        return {
            "workspace_id": str(workspace_id),
            "api_prefix": self._settings.api_prefix,
            "connector": {
                "name": "chaincloud-self-hosted-worker",
                "protocol_version": 1,
                "recommended_version": _version_string(
                    version_policy.get("recommended_version")
                ),
                "min_version": _version_string(version_policy.get("min_version")),
                "upgrade_url": _version_string(version_policy.get("upgrade_url")),
            },
            "endpoints": {
                "register": f"{self._settings.api_prefix}/self-hosted/register",
                "heartbeat": f"{self._settings.api_prefix}/self-hosted/heartbeat",
                "next_job": f"{self._settings.api_prefix}/self-hosted/jobs/next",
                "claim_job": f"{self._settings.api_prefix}/self-hosted/jobs/{{agent_run_id}}/claim",
                "complete_job": (
                    f"{self._settings.api_prefix}/self-hosted/jobs/{{agent_run_id}}/complete"
                ),
                "next_mcp_job": f"{self._settings.api_prefix}/self-hosted/mcp-jobs/next",
                "claim_mcp_job": (
                    f"{self._settings.api_prefix}/self-hosted/mcp-jobs/{{mcp_job_id}}/claim"
                ),
                "complete_mcp_job": (
                    f"{self._settings.api_prefix}/self-hosted/mcp-jobs/{{mcp_job_id}}/complete"
                ),
                "progress": f"{self._settings.api_prefix}/self-hosted/progress",
                "local_files": f"{self._settings.api_prefix}/self-hosted/local-files",
                "artifact_uploads": f"{self._settings.api_prefix}/self-hosted/artifact-uploads",
            },
            "capability_contract": {
                "required": ["machine_id", "name", "version"],
                "optional_capabilities": [
                    "runtime_space_id",
                    "allowed_runtime_space_ids",
                    "allowed_tools",
                    "supported_models",
                    "supported_runtimes",
                    "supported_network_modes",
                    "max_concurrent_jobs",
                    "max_concurrent_mcp_jobs",
                    "max_artifact_bytes",
                ],
            },
            "security": {
                "enrollment_token_transport": "one_time_registration_payload",
                "runtime_credential_transport": "authorization_bearer_token",
                "returns_credentials": False,
                "workspace_scoped": True,
            },
            "version_policy": {
                key: value
                for key, value in version_policy.items()
                if key in {"min_version", "recommended_version", "upgrade_url"}
            },
        }

    def _workspace_self_hosted_version_policy(self, workspace_id: UUID) -> dict[str, object]:
        workspace = self._session.get(Workspace, workspace_id)
        settings = workspace.settings if workspace is not None else None
        if not isinstance(settings, dict):
            return {}
        for key in (
            "self_hosted_worker_policy",
            "self_hosted_connector_policy",
            "self_hosted",
        ):
            policy = settings.get(key)
            if not isinstance(policy, dict):
                continue
            version_policy = policy.get("version_policy")
            if isinstance(version_policy, dict):
                return version_policy
            if "min_version" in policy or "recommended_version" in policy:
                return policy
        return {}

    def _consume_enrollment_token(self, raw_token: str) -> RuntimeEnrollmentToken:
        token = self._session.scalar(
            select(RuntimeEnrollmentToken).where(
                RuntimeEnrollmentToken.token_hash == self._hash(raw_token),
                RuntimeEnrollmentToken.status == "active",
            )
        )
        if token is None:
            raise ValueError("Invalid enrollment token")
        if token.expires_at is not None and token.expires_at < datetime.now(UTC):
            raise ValueError("Enrollment token expired")
        return token

    def _require_worker_run(self, auth: AuthenticatedWorker, agent_run_id: UUID) -> AgentRun:
        run = self._session.get(AgentRun, agent_run_id)
        if (
            run is None
            or run.workspace_id != auth.worker.workspace_id
            or run.runtime_id != auth.runtime.id
        ):
            raise ValueError("Agent run not found for worker")
        return run

    def _append_run_event(
        self,
        run: AgentRun,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> RunEvent:
        next_sequence = (
            self._session.scalar(
                select(func.coalesce(func.max(RunEvent.sequence), 0)).where(
                    RunEvent.workspace_id == run.workspace_id,
                    RunEvent.agent_run_id == run.id,
                )
            )
            or 0
        ) + 1
        event = RunEvent(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            event_type=event_type,
            sequence=next_sequence,
            message=message,
            event_metadata=metadata,
            created_at=datetime.now(UTC),
        )
        self._session.add(event)
        return event

    def _append_runtime_event(
        self,
        runtime: WorkspaceRuntime,
        event_type: str,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> RuntimeEvent:
        event = RuntimeEvent(
            workspace_id=runtime.workspace_id,
            workspace_runtime_id=runtime.id,
            event_type=event_type,
            message=message,
            event_metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        self._session.add(event)
        return event

    def _append_runtime_space_event(
        self,
        runtime: WorkspaceRuntime,
        event_type: str,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if runtime.runtime_space_id is None:
            return
        self._session.add(
            RuntimeSpaceEvent(
                workspace_id=runtime.workspace_id,
                runtime_space_id=runtime.runtime_space_id,
                event_type=event_type,
                message=message,
                event_metadata={"runtime_id": str(runtime.id), **(metadata or {})},
                created_at=datetime.now(UTC),
            )
        )

    def _registration_runtime_space_id(
        self,
        workspace_id: UUID,
        capabilities: dict[str, object],
    ) -> UUID | None:
        runtime_space_id = _uuid_from_capabilities(capabilities, "runtime_space_id")
        if runtime_space_id is None:
            return None
        runtime_space = self._session.get(RuntimeSpace, runtime_space_id)
        if (
            runtime_space is None
            or runtime_space.workspace_id != workspace_id
            or runtime_space.status != "active"
        ):
            raise ValueError("Runtime space not found")
        return runtime_space_id

    def _validated_capabilities(
        self,
        workspace_id: UUID,
        capabilities: dict[str, object],
        *,
        bound_runtime_space_id: UUID | None = None,
    ) -> dict[str, object]:
        normalized = dict(capabilities)
        referenced_ids, invalid_values = _capability_runtime_space_references(normalized)
        if invalid_values:
            raise ValueError(
                "Self-hosted capabilities include invalid runtime space IDs: "
                + ", ".join(invalid_values)
            )
        runtime_space_id = _uuid_from_capabilities(normalized, "runtime_space_id")
        if (
            runtime_space_id is not None
            and bound_runtime_space_id is not None
            and runtime_space_id != bound_runtime_space_id
        ):
            raise ValueError("Self-hosted runtime space binding cannot be changed by heartbeat")
        if bound_runtime_space_id is not None:
            referenced_ids.discard(bound_runtime_space_id)
        if not referenced_ids:
            return normalized
        active_space_ids = {
            runtime_space_id
            for runtime_space_id in self._session.scalars(
                select(RuntimeSpace.id).where(
                    RuntimeSpace.workspace_id == workspace_id,
                    RuntimeSpace.id.in_(referenced_ids),
                    RuntimeSpace.status == "active",
                )
            ).all()
        }
        invalid_ids = sorted(
            str(runtime_space_id) for runtime_space_id in referenced_ids - active_space_ids
        )
        if invalid_ids:
            raise ValueError(
                "Self-hosted capabilities reference unavailable runtime spaces: "
                + ", ".join(invalid_ids)
            )
        return normalized

    def _runtime_space_allowed(self, auth: AuthenticatedWorker, run: AgentRun) -> bool:
        if run.runtime_space_id is None:
            return True
        runtime_space_id = str(run.runtime_space_id)
        if auth.runtime.runtime_space_id is not None and runtime_space_id == str(
            auth.runtime.runtime_space_id
        ):
            return True
        allowed_ids = _string_list(auth.worker.capabilities.get("allowed_runtime_space_ids"))
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
        allowed_tools = _string_list(auth.worker.capabilities.get("allowed_tools"))
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

    def _release_run_reservations(
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

    def _job_claim_for_run(self, run: AgentRun) -> SelfHostedJobClaim | None:
        return self._session.scalar(
            select(SelfHostedJobClaim).where(
                SelfHostedJobClaim.workspace_id == run.workspace_id,
                SelfHostedJobClaim.agent_run_id == run.id,
            )
        )

    def _mark_task_completed_from_self_hosted_run(
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

    def _mark_task_failed_from_self_hosted_run(self, run: AgentRun) -> None:
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = "failed"
        if run.task_id is None:
            return
        task = self._session.get(Task, run.task_id)
        if task is not None and task.workspace_id == run.workspace_id:
            TaskStateService().transition(task, TaskStatus.FAILED, completed_at=run.completed_at)

    def _active_claims_for_worker(self, worker: SelfHostedWorker) -> list[SelfHostedJobClaim]:
        return list(
            self._session.scalars(
                select(SelfHostedJobClaim).where(
                    SelfHostedJobClaim.workspace_id == worker.workspace_id,
                    SelfHostedJobClaim.worker_id == worker.id,
                    SelfHostedJobClaim.status == "claimed",
                )
            ).all()
        )

    def _latest_credential_for_runtime(self, runtime_id: UUID) -> RuntimeCredential | None:
        return self._session.scalar(
            select(RuntimeCredential)
            .where(RuntimeCredential.workspace_runtime_id == runtime_id)
            .order_by(RuntimeCredential.created_at.desc())
            .limit(1)
        )

    def _close_active_claims_for_worker(
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
        for claim in self._active_claims_for_worker(worker):
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
                self._mark_task_failed_from_self_hosted_run(run)
                self._append_run_event(
                    run,
                    event_type,
                    event_message,
                    {
                        "worker_id": str(worker.id),
                        "claim_id": str(claim.id),
                        "reason": reason,
                    },
                )
            self._release_run_reservations(run, released_at=now)
        return affected_claims, affected_runs

    def _require_mcp_job(self, auth: AuthenticatedWorker, mcp_job_id: UUID) -> SelfHostedMcpJob:
        job = self._session.get(SelfHostedMcpJob, mcp_job_id)
        if (
            job is None
            or job.workspace_id != auth.worker.workspace_id
            or job.workspace_runtime_id != auth.runtime.id
        ):
            raise ValueError("Self-hosted MCP job not found")
        return job

    def _expire_stale_job_claims(
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
                self._mark_task_failed_from_self_hosted_run(run)
                self._append_run_event(
                    run,
                    "self_hosted.job_claim_expired",
                    "Self-hosted job claim expired before completion.",
                    {
                        "claim_id": str(claim.id),
                        "worker_id": str(claim.worker_id),
                        "expired_at": now.isoformat(),
                    },
                )
            self._release_run_reservations(run, released_at=now)
        return expired

    def _expire_stale_mcp_jobs(
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
            self._record_mcp_job_completion_for_run(run, job)
            self._append_run_event(
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

    def _record_mcp_job_completion_for_run(
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
                "completed_at": _dt_iso(job.completed_at),
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

    def _hash(self, token: str) -> str:
        material = f"{self._settings.token_hash_pepper}:{token}"
        return sha256(material.encode("utf-8")).hexdigest()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _dt_iso(value: datetime | None) -> str | None:
    utc_value = _as_utc(value)
    return utc_value.isoformat() if utc_value is not None else None


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


def _uuid_from_capabilities(capabilities: dict[str, object], key: str) -> UUID | None:
    value = capabilities.get(key)
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _capability_runtime_space_references(
    capabilities: dict[str, object],
) -> tuple[set[UUID], list[str]]:
    ids: set[UUID] = set()
    invalid_values: list[str] = []
    raw_runtime_space_id = capabilities.get("runtime_space_id")
    if raw_runtime_space_id is not None:
        if not isinstance(raw_runtime_space_id, str):
            invalid_values.append(str(raw_runtime_space_id))
        else:
            try:
                ids.add(UUID(raw_runtime_space_id))
            except ValueError:
                invalid_values.append(raw_runtime_space_id)
    raw_allowed_ids = capabilities.get("allowed_runtime_space_ids")
    if raw_allowed_ids is None:
        return ids, invalid_values
    if not isinstance(raw_allowed_ids, list):
        invalid_values.append(str(raw_allowed_ids))
        return ids, invalid_values
    for raw_id in raw_allowed_ids:
        if not isinstance(raw_id, str):
            invalid_values.append(str(raw_id))
            continue
        try:
            ids.add(UUID(raw_id))
        except ValueError:
            invalid_values.append(raw_id)
    return ids, invalid_values


def _worker_trust_state(
    worker: SelfHostedWorker,
    runtime: WorkspaceRuntime,
    credential: RuntimeCredential | None,
) -> str:
    if credential is not None and credential.status == "revoked":
        return "revoked"
    if worker.status == "revoked" or runtime.status == "revoked":
        return "revoked"
    if worker.status == "quarantined" or runtime.status == "quarantined":
        return "quarantined"
    if worker.status == "degraded" or runtime.connection_status == "degraded":
        return "degraded"
    if worker.status in {"offline", "disabled"} or runtime.connection_status == "offline":
        return "offline"
    return "active"


def _worker_policy_summary(capabilities: dict[str, object]) -> dict[str, object]:
    return {
        "allowed_tools": _string_list(capabilities.get("allowed_tools")),
        "supported_models": _string_list(capabilities.get("supported_models")),
        "supported_runtimes": _string_list(capabilities.get("supported_runtimes"))
        or _string_list(capabilities.get("runtime_types")),
        "supported_network_modes": _string_list(capabilities.get("supported_network_modes"))
        or _string_list(capabilities.get("network_modes")),
        "allowed_runtime_space_ids": _string_list(capabilities.get("allowed_runtime_space_ids")),
        "max_concurrent_jobs": _positive_int(capabilities.get("max_concurrent_jobs")),
        "max_concurrent_mcp_jobs": _positive_int(capabilities.get("max_concurrent_mcp_jobs")),
        "max_artifact_bytes": _positive_int(capabilities.get("max_artifact_bytes")),
    }


def _worker_policy_diagnostics(
    worker: SelfHostedWorker,
    runtime: WorkspaceRuntime,
    *,
    version_policy: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    worker_policy = _worker_policy_summary(worker.capabilities)
    runtime_policy = _worker_policy_summary(runtime.capabilities)
    if worker_policy != runtime_policy:
        diagnostics.append(
            {
                "code": "policy_summary_mismatch",
                "severity": "warning",
                "message": "Worker and runtime policy summaries differ.",
                "worker_policy": worker_policy,
                "runtime_policy": runtime_policy,
            }
        )
    worker_runtime_space_id = _uuid_from_capabilities(worker.capabilities, "runtime_space_id")
    if (
        worker_runtime_space_id is not None
        and runtime.runtime_space_id is not None
        and worker_runtime_space_id != runtime.runtime_space_id
    ):
        diagnostics.append(
            {
                "code": "runtime_space_mismatch",
                "severity": "critical",
                "message": "Worker capability runtime space does not match runtime space.",
                "worker_runtime_space_id": str(worker_runtime_space_id),
                "runtime_space_id": str(runtime.runtime_space_id),
            }
        )
    if worker.status != "revoked" and runtime.status == "revoked":
        diagnostics.append(
            {
                "code": "runtime_revoked_worker_not_revoked",
                "severity": "critical",
                "message": "Runtime is revoked but worker record is not revoked.",
            }
        )
    diagnostics.extend(_worker_version_diagnostics(worker, version_policy or {}))
    return diagnostics


def _worker_version_diagnostics(
    worker: SelfHostedWorker,
    version_policy: dict[str, object],
) -> list[dict[str, object]]:
    current = _parse_version(worker.version)
    if current is None:
        return []
    min_version = _version_string(version_policy.get("min_version"))
    recommended_version = _version_string(version_policy.get("recommended_version"))
    upgrade_url = _version_string(version_policy.get("upgrade_url"))
    diagnostics: list[dict[str, object]] = []
    if min_version is not None:
        parsed_min = _parse_version(min_version)
        if parsed_min is not None and _version_less_than(current, parsed_min):
            diagnostics.append(
                {
                    "code": "self_hosted_connector_upgrade_required",
                    "severity": "critical",
                    "message": "Self-hosted connector version is below minimum supported version.",
                    "current_version": worker.version,
                    "min_version": min_version,
                    "recommended_version": recommended_version,
                    "upgrade_url": upgrade_url,
                }
            )
            return diagnostics
    if recommended_version is not None:
        parsed_recommended = _parse_version(recommended_version)
        if parsed_recommended is not None and _version_less_than(current, parsed_recommended):
            diagnostics.append(
                {
                    "code": "self_hosted_connector_upgrade_recommended",
                    "severity": "warning",
                    "message": "Self-hosted connector version is below recommended version.",
                    "current_version": worker.version,
                    "recommended_version": recommended_version,
                    "upgrade_url": upgrade_url,
                }
            )
    return diagnostics


def _parse_version(value: object) -> tuple[int, ...] | None:
    text = _version_string(value)
    if text is None:
        return None
    normalized = text.removeprefix("v").replace("-", ".")
    parts: list[int] = []
    for raw_part in normalized.split("."):
        digits = "".join(char for char in raw_part if char.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else None


def _version_less_than(current: tuple[int, ...], required: tuple[int, ...]) -> bool:
    length = max(len(current), len(required))
    padded_current = current + (0,) * (length - len(current))
    padded_required = required + (0,) * (length - len(required))
    return padded_current < padded_required


def _version_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _connection_status_after_resume(last_heartbeat_at: datetime | None, now: datetime) -> str:
    heartbeat_at = _as_utc(last_heartbeat_at)
    if heartbeat_at is None:
        return "offline"
    if heartbeat_at < now - timedelta(minutes=5):
        return "offline"
    return "online"
