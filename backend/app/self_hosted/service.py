from dataclasses import dataclass
from datetime import UTC, datetime
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
    ProgressEventRequest,
    RuntimeRegistrationRequest,
    WorkerHeartbeatRequest,
)
from backend.app.core.config import Settings
from backend.app.files.security import safe_filename, validate_storage_key
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime
from backend.app.self_hosted.models import (
    LocalFileReference,
    RuntimeCredential,
    RuntimeEnrollmentToken,
    SelfHostedArtifactUpload,
    SelfHostedJobClaim,
    SelfHostedWorker,
)
from backend.app.tasks.models import Task
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
        auth.worker.status = data.status
        auth.worker.capabilities = data.capabilities or auth.worker.capabilities
        auth.worker.last_heartbeat_at = now
        auth.runtime.connection_status = "online" if data.status == "online" else data.status
        auth.runtime.capabilities = auth.worker.capabilities
        auth.runtime.last_heartbeat_at = now
        self._append_runtime_event(auth.runtime, "self_hosted.heartbeat", data.status)
        self._session.commit()
        self._session.refresh(auth.worker)
        return auth.worker

    def poll_job(self, auth: AuthenticatedWorker) -> AgentRun | None:
        self._require_self_hosted_enabled()
        return self._session.scalar(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == auth.worker.workspace_id,
                AgentRun.status == RunStatus.QUEUED.value,
                AgentRun.runtime_id == auth.runtime.id,
            )
            .order_by(AgentRun.created_at.asc())
            .limit(1)
        )

    def claim_job(self, auth: AuthenticatedWorker, agent_run_id: UUID) -> SelfHostedJobClaim:
        self._require_self_hosted_enabled()
        run = self._session.get(AgentRun, agent_run_id)
        if (
            run is None
            or run.workspace_id != auth.worker.workspace_id
            or run.runtime_id != auth.runtime.id
        ):
            raise ValueError("Agent run not available for this worker")
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
        self._session.commit()
        self._session.refresh(claim)
        return claim

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
            artifact_metadata=data.metadata,
        )
        self._session.add(upload)
        self._session.commit()
        self._session.refresh(upload)
        return upload

    def revoke_credential(
        self,
        workspace_id: UUID,
        credential_id: UUID,
    ) -> RuntimeCredential | None:
        credential = self._session.get(RuntimeCredential, credential_id)
        if credential is None or credential.workspace_id != workspace_id:
            return None
        credential.status = "revoked"
        credential.revoked_at = datetime.now(UTC)
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def _require_self_hosted_enabled(self) -> None:
        policy = PlatformPolicyService(self._session).risky_execution_policy()
        if not policy.allow_self_hosted_runtimes:
            raise ValueError("Self-hosted runtimes are disabled by platform safety policy")

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
    ) -> RuntimeEvent:
        event = RuntimeEvent(
            workspace_id=runtime.workspace_id,
            workspace_runtime_id=runtime.id,
            event_type=event_type,
            message=message,
            created_at=datetime.now(UTC),
        )
        self._session.add(event)
        return event

    def _hash(self, token: str) -> str:
        material = f"{self._settings.token_hash_pepper}:{token}"
        return sha256(material.encode("utf-8")).hexdigest()
