from uuid import UUID

from fastapi import APIRouter, Depends, Query

from backend.app.admin.security_events import AdminSecurityEventService
from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.routes.admin.dependencies import admin_security_event_service
from backend.app.api.routes.admin.responses import page_response
from backend.app.api.schemas.admin import AdminSecurityEventResponse
from backend.app.core.pagination import PageParams

router = APIRouter()


@router.get("/security-events", response_model=PageResponse[AdminSecurityEventResponse])
async def list_admin_security_events(
    page: PageParams = Depends(pagination_params),
    severity: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None),
    service: AdminSecurityEventService = Depends(admin_security_event_service),
) -> PageResponse[AdminSecurityEventResponse]:
    items, total = service.list_security_events(
        page,
        severity=severity,
        workspace_id=workspace_id,
    )
    return page_response(items, total, page, AdminSecurityEventResponse)
