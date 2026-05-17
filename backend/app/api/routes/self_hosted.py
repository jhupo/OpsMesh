from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
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
    ProgressEventRequest,
    RuntimeRegistrationRequest,
    RuntimeRegistrationResponse,
    SelfHostedJobResponse,
    WorkerHeartbeatRequest,
    WorkerHeartbeatResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
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
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> EnrollmentTokenCreateResponse:
    created = SelfHostedRuntimeService(session).create_enrollment_token(
        context.workspace.id,
        context.user.user_id,
        request,
    )
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
) -> RuntimeRegistrationResponse:
    try:
        registered = SelfHostedRuntimeService(session).register_runtime(request)
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
) -> WorkerHeartbeatResponse:
    worker = SelfHostedRuntimeService(session).heartbeat(auth, request)
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


@router.get("/self-hosted/jobs/next", response_model=SelfHostedJobResponse | None)
async def poll_job(
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    session: Session = Depends(get_db_session),
) -> SelfHostedJobResponse | None:
    run = SelfHostedRuntimeService(session).poll_job(auth)
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
) -> JobClaimResponse:
    try:
        claim = SelfHostedRuntimeService(session).claim_job(auth, agent_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return JobClaimResponse(
        claim_id=claim.id,
        agent_run_id=claim.agent_run_id,
        status=claim.status,
        claimed_at=claim.claimed_at,
    )


@router.post("/self-hosted/progress", response_model=RunEventResponse)
async def upload_progress(
    request: ProgressEventRequest,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    session: Session = Depends(get_db_session),
) -> RunEventResponse:
    try:
        event = SelfHostedRuntimeService(session).upload_progress(auth, request)
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
) -> LocalFileReferenceResponse:
    try:
        reference = SelfHostedRuntimeService(session).create_local_file_reference(auth, request)
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
) -> ArtifactUploadResponse:
    try:
        upload = SelfHostedRuntimeService(session).register_artifact_upload(auth, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ArtifactUploadResponse.model_validate(upload)


@router.post(
    "/workspaces/{workspace_id}/self-hosted/credentials/{credential_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_runtime_credential(
    credential_id: UUID,
    _: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> None:
    revoked = SelfHostedRuntimeService(session).revoke_credential(_.workspace.id, credential_id)
    if revoked is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
