from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.api.routes.self_hosted.dependencies import self_hosted_service
from backend.app.api.schemas.self_hosted import (
    McpJobClaimResponse,
    McpJobCompleteRequest,
    McpJobCompleteResponse,
    SelfHostedMcpJobResponse,
)
from backend.app.self_hosted.dependencies import get_authenticated_worker
from backend.app.self_hosted.service import SelfHostedRuntimeService
from backend.app.self_hosted.types import AuthenticatedWorker

router = APIRouter()


@router.get("/self-hosted/mcp-jobs/next", response_model=SelfHostedMcpJobResponse | None)
async def poll_mcp_job(
    auth: AuthenticatedWorker = Depends(get_authenticated_worker),
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> SelfHostedMcpJobResponse | None:
    try:
        job = service.poll_mcp_job(auth)
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
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> McpJobClaimResponse:
    try:
        job = service.claim_mcp_job(auth, mcp_job_id)
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
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> McpJobCompleteResponse:
    try:
        job = service.complete_mcp_job(auth, mcp_job_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if job.completed_at is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MCP job completion failed",
        )
    return McpJobCompleteResponse(id=job.id, status=job.status, completed_at=job.completed_at)
