from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.runtime.instances.admin_dependencies import admin_runtime_service
from opsmesh.runtime.instances.admin_service import AdminRuntimeService
from opsmesh.runtime.spaces.admin_schemas import (
    AdminQuarantineRuntimeSpaceRequest,
    AdminQuarantineRuntimeSpaceResponse,
)
from opsmesh.runtime.spaces.contracts import RuntimeSpaceResponse
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.http.responses import page_response
from opsmesh.shared.pagination import PageParams

router = APIRouter(dependencies=[Depends(require_platform_admin)])


@router.get("/runtime-spaces", response_model=PageResponse[RuntimeSpaceResponse])
def list_admin_runtime_spaces(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None),
    service: AdminRuntimeService = Depends(admin_runtime_service),
) -> PageResponse[RuntimeSpaceResponse]:
    items, total = service.list_runtime_spaces(
        page,
        status=status,
        workspace_id=workspace_id,
    )
    return page_response(items, total, page, RuntimeSpaceResponse)


@router.post(
    "/runtime-spaces/{runtime_space_id}/quarantine",
    response_model=AdminQuarantineRuntimeSpaceResponse,
)
def quarantine_admin_runtime_space(
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
