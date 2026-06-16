from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.admin.runtime_control import AdminRuntimeService
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.routes.admin.dependencies import admin_runtime_service
from backend.app.api.routes.admin.responses import page_response
from backend.app.api.schemas.admin import (
    AdminQuarantineRuntimeSpaceRequest,
    AdminQuarantineRuntimeSpaceResponse,
    AdminRuntimeSpaceResponse,
)

router = APIRouter()


@router.get("/runtime-spaces", response_model=PageResponse[AdminRuntimeSpaceResponse])
async def list_admin_runtime_spaces(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None),
    service: AdminRuntimeService = Depends(admin_runtime_service),
) -> PageResponse[AdminRuntimeSpaceResponse]:
    items, total = service.list_runtime_spaces(
        page,
        status=status,
        workspace_id=workspace_id,
    )
    return page_response(items, total, page, AdminRuntimeSpaceResponse)


@router.post(
    "/runtime-spaces/{runtime_space_id}/quarantine",
    response_model=AdminQuarantineRuntimeSpaceResponse,
)
async def quarantine_admin_runtime_space(
    runtime_space_id: UUID,
    request: AdminQuarantineRuntimeSpaceRequest,
    service: AdminRuntimeService = Depends(admin_runtime_service),
) -> AdminQuarantineRuntimeSpaceResponse:
    runtime_space = service.quarantine_runtime_space(runtime_space_id, reason=request.reason)
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
