from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.admin import AdminUserResponse, AdminUserStatusUpdateRequest
from backend.app.db.session import get_db_session
from backend.app.identity.admin_service import IdentityAdminService
from backend.app.security.service import SecurityAuditService

router = APIRouter()


@router.get("/users", response_model=PageResponse[AdminUserResponse])
async def list_admin_users(
    page: PageParams = Depends(pagination_params),
    user_status: Literal["active", "disabled"] | None = Query(default=None, alias="status"),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminUserResponse]:
    users, total = IdentityAdminService(session).list_users(page, status=user_status)
    return PageResponse(
        items=[AdminUserResponse.model_validate(user) for user in users],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.put("/users/{user_id}/status", response_model=AdminUserResponse)
async def update_admin_user_status(
    user_id: UUID,
    payload: AdminUserStatusUpdateRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> AdminUserResponse:
    if payload.status not in {"active", "disabled"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="User status must be active or disabled",
        )
    user = IdentityAdminService(session).set_user_status(user_id, status=payload.status)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    SecurityAuditService(session).record_request_event(
        request=request,
        action="identity.user_status_changed",
        outcome="allowed",
        severity="warning" if payload.status == "disabled" else "info",
        reason=f"Platform administrator changed user status to {payload.status}",
        user_id=user.id,
        metadata={"status": payload.status, "actor": "platform_admin"},
    )
    session.commit()
    return AdminUserResponse.model_validate(user)
