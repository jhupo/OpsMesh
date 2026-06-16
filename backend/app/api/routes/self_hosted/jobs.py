from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.api.routes.self_hosted.dependencies import (
    self_hosted_dispatch_service,
    self_hosted_progress_service,
    self_hosted_run_completion_service,
)
from backend.app.api.schemas.runs import RunEventResponse
from backend.app.api.schemas.self_hosted import (
    JobClaimResponse,
    JobCompleteRequest,
    JobCompleteResponse,
    ProgressEventRequest,
    SelfHostedJobResponse,
)
from backend.app.self_hosted.dependencies import get_authenticated_worker
from backend.app.self_hosted.dispatch import SelfHostedDispatchService
from backend.app.self_hosted.job_completion import SelfHostedRunCompletionService
from backend.app.self_hosted.progress import SelfHostedProgressService
from backend.app.self_hosted.types import AuthenticatedWorker

router = APIRouter()


@router.get("/self-hosted/jobs/next", response_model=SelfHostedJobResponse | None)
async def poll_job(
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    service: SelfHostedDispatchService = Depends(self_hosted_dispatch_service),
) -> SelfHostedJobResponse | None:
    try:
        run = service.poll_job(auth)
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
    service: SelfHostedDispatchService = Depends(self_hosted_dispatch_service),
) -> JobClaimResponse:
    try:
        claim = service.claim_job(auth, agent_run_id)
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


@router.post("/self-hosted/jobs/{agent_run_id}/complete", response_model=JobCompleteResponse)
async def complete_job(
    agent_run_id: UUID,
    request: JobCompleteRequest,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    service: SelfHostedRunCompletionService = Depends(self_hosted_run_completion_service),
) -> JobCompleteResponse:
    try:
        claim = service.complete_job(auth, agent_run_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if claim.completed_at is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Self-hosted job completion failed",
        )
    return JobCompleteResponse(
        claim_id=claim.id,
        agent_run_id=claim.agent_run_id,
        status=claim.status,
        completed_at=claim.completed_at,
    )


@router.post("/self-hosted/progress", response_model=RunEventResponse)
async def upload_progress(
    request: ProgressEventRequest,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    service: SelfHostedProgressService = Depends(self_hosted_progress_service),
) -> RunEventResponse:
    try:
        event = service.upload_progress(auth, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return RunEventResponse.model_validate(event)
