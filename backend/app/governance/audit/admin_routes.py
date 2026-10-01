from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.governance.audit.query_service import AdminSystemLogService
from backend.app.governance.audit.schemas import AdminSystemLogResponse
from backend.app.identity.authorization.admin_dependencies import require_platform_admin
from backend.app.shared.db.session import get_db_session
from backend.app.shared.http.pagination import PageResponse, pagination_params
from backend.app.shared.http.responses import page_response
from backend.app.shared.pagination import PageParams

router = APIRouter(dependencies=[Depends(require_platform_admin)])





@router.get(
    "/system/logs",
    response_model=PageResponse[AdminSystemLogResponse],
)
async def list_admin_system_logs(
    page: PageParams = Depends(pagination_params),
    workspace_id: UUID | None = Query(default=None),
    user_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None, min_length=1, max_length=120),
    actor_type: str | None = Query(default=None, min_length=1, max_length=32),
    target_type: str | None = Query(default=None, min_length=1, max_length=120),
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminSystemLogResponse]:
    items, total = AdminSystemLogService(session).list_audit_events(
        page,
        workspace_id=workspace_id,
        user_id=user_id,
        action=action,
        actor_type=actor_type,
        target_type=target_type,
        created_after=created_after,
        created_before=created_before,
    )
    return page_response(items, total, page, AdminSystemLogResponse)
