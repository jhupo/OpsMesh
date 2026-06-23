from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.api.routes.self_hosted.dependencies import self_hosted_service
from backend.app.api.routes.self_hosted.responses import (
    heartbeat_response,
    runtime_registration_response,
)
from backend.app.api.schemas.self_hosted import (
    RuntimeRegistrationRequest,
    RuntimeRegistrationResponse,
    WorkerHeartbeatRequest,
    WorkerHeartbeatResponse,
)
from backend.app.self_hosted.dependencies import get_authenticated_worker
from backend.app.self_hosted.service import SelfHostedRuntimeService
from backend.app.self_hosted.types import AuthenticatedWorker

router = APIRouter()


@router.post(
    "/self-hosted/register",
    response_model=RuntimeRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_self_hosted_runtime(
    request: RuntimeRegistrationRequest,
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> RuntimeRegistrationResponse:
    try:
        registered = service.register_runtime(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return runtime_registration_response(registered)


@router.post("/self-hosted/heartbeat", response_model=WorkerHeartbeatResponse)
async def heartbeat(
    request: WorkerHeartbeatRequest,
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> WorkerHeartbeatResponse:
    try:
        worker = service.heartbeat(auth, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if worker.last_heartbeat_at is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Heartbeat failed",
        )
    return heartbeat_response(worker)
