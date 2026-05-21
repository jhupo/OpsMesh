from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.admin.policies import PlatformPolicyService
from backend.app.api.schemas.self_hosted import (
    ArtifactUploadRequest,
    EnrollmentTokenCreateRequest,
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


@dataclass(frozen=True)
class WorkerTrustSnapshot:
    worker: SelfHostedWorker
    runtime: WorkspaceRuntime
    credential: RuntimeCredential | None
    trust_state: str
    policy_summary: dict[str, object]


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
        runtime = WorkspaceRuntime(
            workspace_id=token.workspace_id,
            runtime_space_id=self._registration_runtime_space_id(token.workspace_id, data),
            runtime_provider="self_hosted",
            runtime_type="self_hosted",
            name=data.name,
            status="active",
            connection_status="online",
            capabilities=data.capabilities,
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
            capabilities=data.capabilities,
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
        auth.worker.status = data.status
        auth.worker.capabilities = data.capabilities or auth.worker.capabilities
        auth.worker.last_heartbeat_at = now
        auth.runtime.connection_status = "online" if data.status == "online" else data.status
        auth.runtime.capabilities = auth.worker.capabilities
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
        self._require_worker_accepting_jobs(auth)
        run = self._session.get(AgentRun, agent_run_id)
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
        if not self._worker_capacity_allows(auth):
            raise ValueError("Self-hosted worker has reached max concurrent jobs")
        if run.status != RunStatus.QUEUED.value:
            raise ValueError("Agent run is not queued")
        now = datetime.now(UTC)
        run.status = RunStatus.RUNNING.value
        run.started_at = now
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
        job = self._session.scalar(
            select(SelfHostedMcpJob)
            .where(
                SelfHostedMcpJob.workspace_id == auth.worker.workspace_id,
                SelfHostedMcpJob.workspace_runtime_id == auth.runtime.id,
                SelfHostedMcpJob.status == "queued",
            )
            .order_by(SelfHostedMcpJob.created_at.asc())
            .limit(1)
        )
        if job is not None and not self._worker_can_accept_mcp_job(auth, job):
            return None
        return job

    def claim_mcp_job(self, auth: AuthenticatedWorker, mcp_job_id: UUID) -> SelfHostedMcpJob:
        self._require_self_hosted_enabled()
        self._require_worker_accepting_jobs(auth)
        job = self._require_mcp_job(auth, mcp_job_id)
        if not self._worker_mcp_capacity_allows(auth):
            raise ValueError("Self-hosted worker has reached max concurrent MCP jobs")
        if not self._worker_can_accept_mcp_job(auth, job):
            raise ValueError("Self-hosted MCP job is not compatible with worker")
        if job.status != "queued":
            raise ValueError("Self-hosted MCP job is not queued")
        job.status = "claimed"
        job.worker_id = auth.worker.id
        job.claimed_at = datetime.now(UTC)
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
            file_metadata=data.metadata,
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
    ) -> WorkerTrustCleanupResult:
        now = datetime.now(UTC)
        degraded_cutoff = now - timedelta(seconds=stale_after_seconds)
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
        self._session.commit()
        return WorkerTrustCleanupResult(degraded=degraded, quarantined=quarantined)

    def list_worker_trust(self, workspace_id: UUID) -> list[WorkerTrustSnapshot]:
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
                )
            )
        return snapshots

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
        data: RuntimeRegistrationRequest,
    ) -> UUID | None:
        runtime_space_id = _uuid_from_capabilities(data.capabilities, "runtime_space_id")
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

    def _require_mcp_job(self, auth: AuthenticatedWorker, mcp_job_id: UUID) -> SelfHostedMcpJob:
        job = self._session.get(SelfHostedMcpJob, mcp_job_id)
        if (
            job is None
            or job.workspace_id != auth.worker.workspace_id
            or job.workspace_runtime_id != auth.runtime.id
        ):
            raise ValueError("Self-hosted MCP job not found")
        return job

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
        elif job.status == "failed" and run.status == RunStatus.WAITING_RUNTIME.value:
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


def _uuid_from_capabilities(capabilities: dict[str, object], key: str) -> UUID | None:
    value = capabilities.get(key)
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


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
