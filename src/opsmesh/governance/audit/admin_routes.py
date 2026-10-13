from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from opsmesh.governance.audit.admin_queries import AdminAuditQueryService
from opsmesh.governance.audit.schemas import AuditEventResponse
from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.http.responses import page_response
from opsmesh.shared.pagination import PageParams

router = APIRouter(dependencies=[Depends(require_platform_admin)])


@router.get(
    "/system/logs",
    response_model=PageResponse[AuditEventResponse],
)
def list_admin_system_logs(
    page: PageParams = Depends(pagination_params),
    workspace_id: UUID | None = Query(default=None),
    user_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None, min_length=1, max_length=120),
    actor_type: str | None = Query(default=None, min_length=1, max_length=32),
    target_type: str | None = Query(default=None, min_length=1, max_length=120),
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AuditEventResponse]:
    items, total = AdminAuditQueryService(session).list_audit_events(
        page,
        workspace_id=workspace_id,
        user_id=user_id,
        action=action,
        actor_type=actor_type,
        target_type=target_type,
        created_after=created_after,
        created_before=created_before,
    )
    return page_response(items, total, page, AuditEventResponse)
