from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.schemas.self_hosted import (
    ArtifactUploadRequest,
    EnrollmentTokenCreateRequest,
    JobCompleteRequest,
    LocalFileReferenceRequest,
    McpJobCompleteRequest,
    RuntimeRegistrationRequest,
    WorkerHeartbeatRequest,
)
from backend.app.core.config import Settings
from backend.app.runs.models import AgentRun
from backend.app.self_hosted.artifacts import SelfHostedArtifactService
from backend.app.self_hosted.dispatch import SelfHostedDispatchService
from backend.app.self_hosted.events import SelfHostedEventRecorder
from backend.app.self_hosted.identity import SelfHostedIdentityService
from backend.app.self_hosted.job_completion import SelfHostedRunCompletionService
from backend.app.self_hosted.jobs import SelfHostedJobFinalizer
from backend.app.self_hosted.maintenance import SelfHostedMaintenanceService
from backend.app.self_hosted.mcp_jobs import SelfHostedMcpJobService
from backend.app.self_hosted.models import (
    LocalFileReference,
    RuntimeCredential,
    SelfHostedArtifactUpload,
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)
from backend.app.self_hosted.policy_gate import SelfHostedPolicyGate
from backend.app.self_hosted.trust import SelfHostedTrustService, WorkerTrustSnapshot
from backend.app.self_hosted.types import (
    AuthenticatedWorker,
    CreatedEnrollmentToken,
    RegisteredRuntime,
    WorkerControlResult,
    WorkerTrustCleanupResult,
)
from backend.app.self_hosted.worker_control import SelfHostedWorkerControlService


class SelfHostedRuntimeService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._events = SelfHostedEventRecorder(session)
        self._identity = SelfHostedIdentityService(session, settings, self._events)
        self._jobs = SelfHostedJobFinalizer(session, self._events)
        self._dispatch = SelfHostedDispatchService(session, self._events, self._jobs)
        self._policy_gate = SelfHostedPolicyGate(session)
        self._worker_control = SelfHostedWorkerControlService(
            session,
            self._events,
            self._jobs,
        )
        self._maintenance = SelfHostedMaintenanceService(session, self._events, self._jobs)

    def create_enrollment_token(
        self,
        workspace_id: UUID,
        user_id: UUID,
        data: EnrollmentTokenCreateRequest,
    ) -> CreatedEnrollmentToken:
        self._policy_gate.require_enabled()
        return self._identity.create_enrollment_token(workspace_id, user_id, data)

    def register_runtime(self, data: RuntimeRegistrationRequest) -> RegisteredRuntime:
        self._policy_gate.require_enabled()
        return self._identity.register_runtime(data)

    def authenticate_worker(self, credential_token: str) -> AuthenticatedWorker:
        return self._identity.authenticate_worker(credential_token)

    def heartbeat(
        self,
        auth: AuthenticatedWorker,
        data: WorkerHeartbeatRequest,
    ) -> SelfHostedWorker:
        return self._identity.heartbeat(auth, data)

    def create_local_file_reference(
        self,
        auth: AuthenticatedWorker,
        data: LocalFileReferenceRequest,
    ) -> LocalFileReference:
        return SelfHostedArtifactService(self._session).create_local_file_reference(auth, data)

    def register_artifact_upload(
        self,
        auth: AuthenticatedWorker,
        data: ArtifactUploadRequest,
    ) -> SelfHostedArtifactUpload:
        return SelfHostedArtifactService(self._session).register_artifact_upload(auth, data)

    def poll_job(self, auth: AuthenticatedWorker) -> AgentRun | None:
        return self._dispatch.poll_job(auth)

    def claim_job(self, auth: AuthenticatedWorker, agent_run_id: UUID) -> SelfHostedJobClaim:
        return self._dispatch.claim_job(auth, agent_run_id)

    def complete_job(
        self,
        auth: AuthenticatedWorker,
        agent_run_id: UUID,
        data: JobCompleteRequest,
    ) -> SelfHostedJobClaim:
        return SelfHostedRunCompletionService(self._session).complete_job(
            auth,
            agent_run_id,
            data,
        )

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
        return SelfHostedMcpJobService(self._session).create_mcp_job(
            workspace_id=workspace_id,
            runtime_id=runtime_id,
            agent_run_id=agent_run_id,
            mcp_server_id=mcp_server_id,
            tool_name=tool_name,
            request_payload=request_payload,
        )

    def complete_mcp_job(
        self,
        auth: AuthenticatedWorker,
        mcp_job_id: UUID,
        data: McpJobCompleteRequest,
    ) -> SelfHostedMcpJob:
        return SelfHostedMcpJobService(self._session).complete_mcp_job(
            auth,
            mcp_job_id,
            data,
        )

    def poll_mcp_job(self, auth: AuthenticatedWorker) -> SelfHostedMcpJob | None:
        return self._dispatch.poll_mcp_job(auth)

    def claim_mcp_job(
        self,
        auth: AuthenticatedWorker,
        mcp_job_id: UUID,
    ) -> SelfHostedMcpJob:
        return self._dispatch.claim_mcp_job(auth, mcp_job_id)

    def revoke_credential(
        self,
        workspace_id: UUID,
        credential_id: UUID,
        *,
        actor_user_id: UUID | None = None,
        reason: str = "",
    ) -> RuntimeCredential | None:
        return self._worker_control.revoke_credential(
            workspace_id,
            credential_id,
            actor_user_id=actor_user_id,
            reason=reason,
        )

    def control_worker(
        self,
        workspace_id: UUID,
        worker_id: UUID,
        *,
        action: str,
        actor_user_id: UUID | None = None,
        reason: str = "",
    ) -> WorkerControlResult | None:
        return self._worker_control.control_worker(
            workspace_id,
            worker_id,
            action=action,
            actor_user_id=actor_user_id,
            reason=reason,
        )

    def cleanup_stale_workers(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int = 600,
        quarantine_after_seconds: int | None = None,
        job_claim_stale_after_seconds: int = 900,
        mcp_job_stale_after_seconds: int = 900,
    ) -> WorkerTrustCleanupResult:
        return self._maintenance.cleanup_stale_workers(
            workspace_id,
            stale_after_seconds=stale_after_seconds,
            quarantine_after_seconds=quarantine_after_seconds,
            job_claim_stale_after_seconds=job_claim_stale_after_seconds,
            mcp_job_stale_after_seconds=mcp_job_stale_after_seconds,
        )

    def list_worker_trust(self, workspace_id: UUID) -> list[WorkerTrustSnapshot]:
        return SelfHostedTrustService(self._session, self._settings).list_worker_trust(
            workspace_id
        )

    def connector_manifest(self, workspace_id: UUID) -> dict[str, object]:
        return SelfHostedTrustService(self._session, self._settings).connector_manifest(
            workspace_id
        )
