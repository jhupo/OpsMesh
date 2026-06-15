from datetime import UTC, datetime
from uuid import UUID

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
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime
from backend.app.self_hosted.artifacts import SelfHostedArtifactService
from backend.app.self_hosted.dispatch import SelfHostedDispatchService
from backend.app.self_hosted.events import SelfHostedEventRecorder
from backend.app.self_hosted.identity import SelfHostedIdentityService
from backend.app.self_hosted.jobs import SelfHostedJobFinalizer
from backend.app.self_hosted.maintenance import SelfHostedMaintenanceService
from backend.app.self_hosted.models import (
    LocalFileReference,
    RuntimeCredential,
    SelfHostedArtifactUpload,
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)
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
        self._require_self_hosted_enabled()
        return self._identity.create_enrollment_token(workspace_id, user_id, data)

    def register_runtime(self, data: RuntimeRegistrationRequest) -> RegisteredRuntime:
        self._require_self_hosted_enabled()
        return self._identity.register_runtime(data)

    def authenticate_worker(self, credential_token: str) -> AuthenticatedWorker:
        return self._identity.authenticate_worker(credential_token)

    def heartbeat(
        self,
        auth: AuthenticatedWorker,
        data: WorkerHeartbeatRequest,
    ) -> SelfHostedWorker:
        return self._identity.heartbeat(auth, data)

    def poll_job(self, auth: AuthenticatedWorker) -> AgentRun | None:
        self._require_self_hosted_enabled()
        return self._dispatch.poll_job(auth)

    def claim_job(self, auth: AuthenticatedWorker, agent_run_id: UUID) -> SelfHostedJobClaim:
        self._require_self_hosted_enabled()
        return self._dispatch.claim_job(auth, agent_run_id)

    def complete_job(
        self,
        auth: AuthenticatedWorker,
        agent_run_id: UUID,
        data: JobCompleteRequest,
    ) -> SelfHostedJobClaim:
        run = self._require_worker_run(auth, agent_run_id)
        claim = self._jobs.job_claim_for_run(run)
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
            self._jobs.mark_task_completed_from_run(run, data.output)
        else:
            run.status = RunStatus.FAILED.value
            run.error = data.error or {
                "code": "self_hosted_job_failed",
                "message": "Self-hosted job failed",
            }
            self._jobs.mark_task_failed_from_run(run)
        self._jobs.release_run_reservations(run, released_at=now)
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
        return self._dispatch.poll_mcp_job(auth)

    def claim_mcp_job(self, auth: AuthenticatedWorker, mcp_job_id: UUID) -> SelfHostedMcpJob:
        self._require_self_hosted_enabled()
        return self._dispatch.claim_mcp_job(auth, mcp_job_id)

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
            self._jobs.record_mcp_job_completion_for_run(run, job)
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
        return SelfHostedArtifactService(self._session).create_local_file_reference(auth, data)

    def register_artifact_upload(
        self,
        auth: AuthenticatedWorker,
        data: ArtifactUploadRequest,
    ) -> SelfHostedArtifactUpload:
        return SelfHostedArtifactService(self._session).register_artifact_upload(auth, data)

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
        return self._events.append_run_event(run, event_type, message, metadata)

    def _append_runtime_event(
        self,
        runtime: WorkspaceRuntime,
        event_type: str,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> RuntimeEvent:
        return self._events.append_runtime_event(runtime, event_type, message, metadata)

    def _append_runtime_space_event(
        self,
        runtime: WorkspaceRuntime,
        event_type: str,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self._events.append_runtime_space_event(runtime, event_type, message, metadata)

    def _require_mcp_job(self, auth: AuthenticatedWorker, mcp_job_id: UUID) -> SelfHostedMcpJob:
        job = self._session.get(SelfHostedMcpJob, mcp_job_id)
        if (
            job is None
            or job.workspace_id != auth.worker.workspace_id
            or job.workspace_runtime_id != auth.runtime.id
        ):
            raise ValueError("Self-hosted MCP job not found")
        return job
