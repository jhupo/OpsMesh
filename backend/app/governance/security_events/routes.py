from uuid import UUID

from fastapi import APIRouter, Depends, Query

from backend.app.governance.security_events.dependencies import admin_security_event_service
from backend.app.governance.security_events.query_service import AdminSecurityEventService
from backend.app.governance.security_events.schemas import AdminSecurityEventResponse
from backend.app.identity.authorization.admin_dependencies import require_platform_admin
from backend.app.shared.http.pagination import PageResponse, pagination_params
from backend.app.shared.http.responses import page_response
from backend.app.shared.pagination import PageParams

router = APIRouter(dependencies=[Depends(require_platform_admin)])


@router.get("/security-events", response_model=PageResponse[AdminSecurityEventResponse])
def list_admin_security_events(
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
