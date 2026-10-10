from uuid import UUID

from fastapi import APIRouter, Depends, Query

from opsmesh.governance.security_events.dependencies import admin_security_event_service
from opsmesh.governance.security_events.query_service import AdminSecurityEventService
from opsmesh.governance.security_events.schemas import AdminSecurityEventResponse
from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.http.responses import page_response
from opsmesh.shared.pagination import PageParams

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
