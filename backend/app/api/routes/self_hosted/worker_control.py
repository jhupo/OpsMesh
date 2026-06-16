from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.app.api.routes.self_hosted.dependencies import self_hosted_service
from backend.app.api.routes.self_hosted.responses import (
    worker_control_response,
    worker_trust_response,
)
from backend.app.api.schemas.self_hosted import (
    RuntimeCredentialRevokeRequest,
    SelfHostedConnectorManifestResponse,
    SelfHostedWorkerCleanupResponse,
    SelfHostedWorkerControlRequest,
    SelfHostedWorkerControlResponse,
    SelfHostedWorkerTrustResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.self_hosted.service import SelfHostedRuntimeService

router = APIRouter()


@router.post(
    "/workspaces/{workspace_id}/self-hosted/worker-cleanup",
    response_model=SelfHostedWorkerCleanupResponse,
)
async def cleanup_self_hosted_workers(
    stale_after_seconds: int = Query(default=600, ge=60, le=86_400),
    quarantine_after_seconds: int | None = Query(default=None, ge=60, le=604_800),
    job_claim_stale_after_seconds: int = Query(default=900, ge=60, le=86_400),
    mcp_job_stale_after_seconds: int = Query(default=900, ge=60, le=86_400),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> SelfHostedWorkerCleanupResponse:
    result = service.cleanup_stale_workers(
        context.workspace.id,
        stale_after_seconds=stale_after_seconds,
        quarantine_after_seconds=quarantine_after_seconds,
        job_claim_stale_after_seconds=job_claim_stale_after_seconds,
        mcp_job_stale_after_seconds=mcp_job_stale_after_seconds,
    )
    return SelfHostedWorkerCleanupResponse(
        degraded=result.degraded,
        quarantined=result.quarantined,
        expired_job_claims=result.expired_job_claims,
        expired_mcp_jobs=result.expired_mcp_jobs,
    )


@router.post(
    "/workspaces/{workspace_id}/self-hosted/workers/{worker_id}/quarantine",
    response_model=SelfHostedWorkerControlResponse,
)
async def quarantine_self_hosted_worker(
    worker_id: UUID,
    request: SelfHostedWorkerControlRequest | None = None,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> SelfHostedWorkerControlResponse:
    return _control_self_hosted_worker(worker_id, request, context, service, action="quarantine")


@router.post(
    "/workspaces/{workspace_id}/self-hosted/workers/{worker_id}/resume",
    response_model=SelfHostedWorkerControlResponse,
)
async def resume_self_hosted_worker(
    worker_id: UUID,
    request: SelfHostedWorkerControlRequest | None = None,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> SelfHostedWorkerControlResponse:
    return _control_self_hosted_worker(worker_id, request, context, service, action="resume")


@router.post(
    "/workspaces/{workspace_id}/self-hosted/workers/{worker_id}/revoke",
    response_model=SelfHostedWorkerControlResponse,
)
async def revoke_self_hosted_worker(
    worker_id: UUID,
    request: SelfHostedWorkerControlRequest | None = None,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> SelfHostedWorkerControlResponse:
    return _control_self_hosted_worker(worker_id, request, context, service, action="revoke")


@router.get(
    "/workspaces/{workspace_id}/self-hosted/workers/trust",
    response_model=list[SelfHostedWorkerTrustResponse],
)
async def list_self_hosted_worker_trust(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> list[SelfHostedWorkerTrustResponse]:
    snapshots = service.list_worker_trust(context.workspace.id)
    return [worker_trust_response(snapshot) for snapshot in snapshots]


@router.get(
    "/workspaces/{workspace_id}/self-hosted/connector-manifest",
    response_model=SelfHostedConnectorManifestResponse,
)
async def self_hosted_connector_manifest(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> SelfHostedConnectorManifestResponse:
    manifest = service.connector_manifest(context.workspace.id)
    return SelfHostedConnectorManifestResponse(**manifest)


@router.post(
    "/workspaces/{workspace_id}/self-hosted/credentials/{credential_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_runtime_credential(
    credential_id: UUID,
    request: RuntimeCredentialRevokeRequest | None = None,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    service: SelfHostedRuntimeService = Depends(self_hosted_service),
) -> None:
    revoked = service.revoke_credential(
        context.workspace.id,
        credential_id,
        actor_user_id=context.user.user_id,
        reason=request.reason if request is not None else "",
    )
    if revoked is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")


def _control_self_hosted_worker(
    worker_id: UUID,
    request: SelfHostedWorkerControlRequest | None,
    context: WorkspaceContext,
    service: SelfHostedRuntimeService,
    *,
    action: str,
) -> SelfHostedWorkerControlResponse:
    try:
        result = service.control_worker(
            context.workspace.id,
            worker_id,
            action=action,
            actor_user_id=context.user.user_id,
            reason=request.reason if request is not None else "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")
    return worker_control_response(result)
