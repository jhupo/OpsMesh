from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.admin.service import AdminControlPlaneService
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.admin import (
    AdminOverviewResponse,
    AdminQuarantineRuntimeSpaceRequest,
    AdminQuarantineRuntimeSpaceResponse,
    AdminRuntimeSpaceResponse,
    AdminSecurityEventResponse,
    AdminWorkerLeaseResponse,
    AdminWorkerNodeResponse,
    AdminWorkspaceResponse,
)
from backend.app.auth.admin import require_platform_admin
from backend.app.db.session import get_db_session

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_platform_admin)],
)


@router.get("/overview", response_model=AdminOverviewResponse)
async def admin_overview(session: Session = Depends(get_db_session)) -> AdminOverviewResponse:
    return AdminOverviewResponse(**AdminControlPlaneService(session).overview())


@router.get("/workspaces", response_model=PageResponse[AdminWorkspaceResponse])
async def list_admin_workspaces(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminWorkspaceResponse]:
    items, total = AdminControlPlaneService(session).list_workspaces(page, status=status)
    return PageResponse(
        items=[AdminWorkspaceResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/workers", response_model=PageResponse[AdminWorkerNodeResponse])
async def list_admin_workers(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    worker_type: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminWorkerNodeResponse]:
    items, total = AdminControlPlaneService(session).list_workers(
        page,
        status=status,
        worker_type=worker_type,
    )
    return PageResponse(
        items=[AdminWorkerNodeResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/workers/{worker_id}/drain", response_model=AdminWorkerNodeResponse)
async def drain_admin_worker(
    worker_id: str,
    session: Session = Depends(get_db_session),
) -> AdminWorkerNodeResponse:
    worker = AdminControlPlaneService(session).drain_worker(worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    return AdminWorkerNodeResponse.model_validate(worker)


@router.get("/runtime-spaces", response_model=PageResponse[AdminRuntimeSpaceResponse])
async def list_admin_runtime_spaces(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminRuntimeSpaceResponse]:
    items, total = AdminControlPlaneService(session).list_runtime_spaces(
        page,
        status=status,
        workspace_id=workspace_id,
    )
    return PageResponse(
        items=[AdminRuntimeSpaceResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/runtime-spaces/{runtime_space_id}/quarantine",
    response_model=AdminQuarantineRuntimeSpaceResponse,
)
async def quarantine_admin_runtime_space(
    runtime_space_id: UUID,
    request: AdminQuarantineRuntimeSpaceRequest,
    session: Session = Depends(get_db_session),
) -> AdminQuarantineRuntimeSpaceResponse:
    runtime_space = AdminControlPlaneService(session).quarantine_runtime_space(
        runtime_space_id,
        reason=request.reason,
    )
    if runtime_space is None:
        raise HTTPException(status_code=404, detail="Runtime space not found")
    return AdminQuarantineRuntimeSpaceResponse(
        id=runtime_space.id,
        created_at=runtime_space.created_at,
        updated_at=runtime_space.updated_at,
        workspace_id=runtime_space.workspace_id,
        name=runtime_space.name,
        scope=runtime_space.scope,
        status=runtime_space.status,
        reason=request.reason,
    )


@router.get("/worker-leases", response_model=PageResponse[AdminWorkerLeaseResponse])
async def list_admin_worker_leases(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None),
    worker_id: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminWorkerLeaseResponse]:
    items, total = AdminControlPlaneService(session).list_worker_leases(
        page,
        status=status,
        workspace_id=workspace_id,
        worker_id=worker_id,
    )
    return PageResponse(
        items=[AdminWorkerLeaseResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/security-events", response_model=PageResponse[AdminSecurityEventResponse])
async def list_admin_security_events(
    page: PageParams = Depends(pagination_params),
    severity: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminSecurityEventResponse]:
    items, total = AdminControlPlaneService(session).list_security_events(
        page,
        severity=severity,
        workspace_id=workspace_id,
    )
    return PageResponse(
        items=[AdminSecurityEventResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
