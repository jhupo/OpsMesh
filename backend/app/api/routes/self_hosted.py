from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.schemas.runs import RunEventResponse
from backend.app.api.schemas.self_hosted import (
    ArtifactUploadRequest,
    ArtifactUploadResponse,
    EnrollmentTokenCreateRequest,
    EnrollmentTokenCreateResponse,
    JobClaimResponse,
    LocalFileReferenceRequest,
    LocalFileReferenceResponse,
    McpJobClaimResponse,
    McpJobCompleteRequest,
    McpJobCompleteResponse,
    ProgressEventRequest,
    RuntimeCredentialRevokeRequest,
    RuntimeRegistrationRequest,
    RuntimeRegistrationResponse,
    SelfHostedJobResponse,
    SelfHostedMcpJobResponse,
    SelfHostedWorkerCleanupResponse,
    SelfHostedWorkerTrustResponse,
    WorkerHeartbeatRequest,
    WorkerHeartbeatResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.self_hosted.dependencies import get_authenticated_worker
from backend.app.self_hosted.service import AuthenticatedWorker, SelfHostedRuntimeService

router = APIRouter(tags=["self-hosted-runtime"])


@router.post(
    "/workspaces/{workspace_id}/self-hosted/enrollment-tokens",
    response_model=EnrollmentTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_enrollment_token(
    request: EnrollmentTokenCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> EnrollmentTokenCreateResponse:
    try:
        created = SelfHostedRuntimeService(session, settings).create_enrollment_token(
            context.workspace.id,
            context.user.user_id,
            request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return EnrollmentTokenCreateResponse(
        id=created.record.id,
        created_at=created.record.created_at,
        updated_at=created.record.updated_at,
        workspace_id=created.record.workspace_id,
        name=created.record.name,
        status=created.record.status,
        expires_at=created.record.expires_at,
        used_at=created.record.used_at,
        token=created.token,
    )


@router.post(
    "/self-hosted/register",
    response_model=RuntimeRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_self_hosted_runtime(
    request: RuntimeRegistrationRequest,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RuntimeRegistrationResponse:
    try:
        registered = SelfHostedRuntimeService(session, settings).register_runtime(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RuntimeRegistrationResponse(
        workspace_id=registered.workspace_id,
        workspace_runtime_id=registered.workspace_runtime_id,
        worker_id=registered.worker_id,
        credential_token=registered.credential_token,
    )


@router.post("/self-hosted/heartbeat", response_model=WorkerHeartbeatResponse)
async def heartbeat(
    request: WorkerHeartbeatRequest,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkerHeartbeatResponse:
    try:
        worker = SelfHostedRuntimeService(session, settings).heartbeat(auth, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if worker.last_heartbeat_at is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Heartbeat failed",
        )
    return WorkerHeartbeatResponse(
        worker_id=worker.id,
        workspace_runtime_id=worker.workspace_runtime_id,
        status=worker.status,
        last_heartbeat_at=worker.last_heartbeat_at,
    )


@router.post(
    "/workspaces/{workspace_id}/self-hosted/worker-cleanup",
    response_model=SelfHostedWorkerCleanupResponse,
)
async def cleanup_self_hosted_workers(
    stale_after_seconds: int = Query(default=600, ge=60, le=86_400),
    quarantine_after_seconds: int | None = Query(default=None, ge=60, le=604_800),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> SelfHostedWorkerCleanupResponse:
    result = SelfHostedRuntimeService(session, settings).cleanup_stale_workers(
        context.workspace.id,
        stale_after_seconds=stale_after_seconds,
        quarantine_after_seconds=quarantine_after_seconds,
    )
    return SelfHostedWorkerCleanupResponse(
        degraded=result.degraded,
        quarantined=result.quarantined,
    )


@router.get(
    "/workspaces/{workspace_id}/self-hosted/workers/trust",
    response_model=list[SelfHostedWorkerTrustResponse],
)
async def list_self_hosted_worker_trust(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[SelfHostedWorkerTrustResponse]:
    snapshots = SelfHostedRuntimeService(session, settings).list_worker_trust(
        context.workspace.id,
    )
    return [
        SelfHostedWorkerTrustResponse(
            worker_id=snapshot.worker.id,
            workspace_runtime_id=snapshot.worker.workspace_runtime_id,
            runtime_space_id=snapshot.runtime.runtime_space_id,
            name=snapshot.worker.name,
            machine_id=snapshot.worker.machine_id,
            version=snapshot.worker.version,
            trust_state=snapshot.trust_state,
            worker_status=snapshot.worker.status,
            runtime_status=snapshot.runtime.status,
            connection_status=snapshot.runtime.connection_status,
            credential_status=snapshot.credential.status if snapshot.credential else None,
            last_heartbeat_at=snapshot.worker.last_heartbeat_at,
            credential_last_used_at=snapshot.credential.last_used_at
            if snapshot.credential
            else None,
            credential_revoked_at=snapshot.credential.revoked_at
            if snapshot.credential
            else None,
            policy_summary=snapshot.policy_summary,
            capabilities=snapshot.worker.capabilities,
        )
        for snapshot in snapshots
    ]


@router.get("/self-hosted/jobs/next", response_model=SelfHostedJobResponse | None)
async def poll_job(
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> SelfHostedJobResponse | None:
    try:
        run = SelfHostedRuntimeService(session, settings).poll_job(auth)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if run is None:
        return None
    return SelfHostedJobResponse(
        agent_run_id=run.id,
        task_id=run.task_id,
        input=run.input,
        model=run.model,
        created_at=run.created_at,
    )


@router.post("/self-hosted/jobs/{agent_run_id}/claim", response_model=JobClaimResponse)
async def claim_job(
    agent_run_id: UUID,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> JobClaimResponse:
    try:
        claim = SelfHostedRuntimeService(session, settings).claim_job(auth, agent_run_id)
    except ValueError as exc:
        status_code = (
            status.HTTP_400_BAD_REQUEST
            if "disabled by platform safety policy" in str(exc)
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return JobClaimResponse(
        claim_id=claim.id,
        agent_run_id=claim.agent_run_id,
        status=claim.status,
        claimed_at=claim.claimed_at,
    )


@router.get("/self-hosted/mcp-jobs/next", response_model=SelfHostedMcpJobResponse | None)
async def poll_mcp_job(
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> SelfHostedMcpJobResponse | None:
    try:
        job = SelfHostedRuntimeService(session, settings).poll_mcp_job(auth)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if job is None:
        return None
    return SelfHostedMcpJobResponse(
        id=job.id,
        agent_run_id=job.agent_run_id,
        mcp_server_id=job.mcp_server_id,
        tool_name=job.tool_name,
        request_payload=job.request_payload,
        created_at=job.created_at,
    )


@router.post("/self-hosted/mcp-jobs/{mcp_job_id}/claim", response_model=McpJobClaimResponse)
async def claim_mcp_job(
    mcp_job_id: UUID,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> McpJobClaimResponse:
    try:
        job = SelfHostedRuntimeService(session, settings).claim_mcp_job(auth, mcp_job_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if job.claimed_at is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MCP job claim failed",
        )
    return McpJobClaimResponse(id=job.id, status=job.status, claimed_at=job.claimed_at)


@router.post(
    "/self-hosted/mcp-jobs/{mcp_job_id}/complete",
    response_model=McpJobCompleteResponse,
)
async def complete_mcp_job(
    mcp_job_id: UUID,
    request: McpJobCompleteRequest,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> McpJobCompleteResponse:
    try:
        job = SelfHostedRuntimeService(session, settings).complete_mcp_job(
            auth,
            mcp_job_id,
            request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if job.completed_at is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MCP job completion failed",
        )
    return McpJobCompleteResponse(id=job.id, status=job.status, completed_at=job.completed_at)


@router.post("/self-hosted/progress", response_model=RunEventResponse)
async def upload_progress(
    request: ProgressEventRequest,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RunEventResponse:
    try:
        event = SelfHostedRuntimeService(session, settings).upload_progress(auth, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return RunEventResponse.model_validate(event)


@router.post(
    "/self-hosted/local-files",
    response_model=LocalFileReferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_local_file_reference(
    request: LocalFileReferenceRequest,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> LocalFileReferenceResponse:
    try:
        reference = SelfHostedRuntimeService(session, settings).create_local_file_reference(
            auth,
            request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return LocalFileReferenceResponse.model_validate(reference)


@router.post(
    "/self-hosted/artifact-uploads",
    response_model=ArtifactUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_artifact_upload(
    request: ArtifactUploadRequest,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ArtifactUploadResponse:
    try:
        upload = SelfHostedRuntimeService(session, settings).register_artifact_upload(auth, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ArtifactUploadResponse.model_validate(upload)


@router.post(
    "/workspaces/{workspace_id}/self-hosted/credentials/{credential_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_runtime_credential(
    credential_id: UUID,
    request: RuntimeCredentialRevokeRequest | None = None,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> None:
    revoked = SelfHostedRuntimeService(session, settings).revoke_credential(
        context.workspace.id,
        credential_id,
        actor_user_id=context.user.user_id,
        reason=request.reason if request is not None else "",
    )
    if revoked is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
