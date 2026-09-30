from secrets import token_urlsafe
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from backend.app.api.client_ip import security_request_context
from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.platform.admin import (
    AdminUserCreateRequest,
    AdminUserCreateResponse,
    AdminUserDetailResponse,
    AdminUserListResponse,
    AdminUserPasswordResetResponse,
    AdminUserResponse,
    AdminUserStatusUpdateRequest,
    AdminUserTokenRevokeResponse,
    AdminUserUpdateRequest,
    AdminWorkspaceMemberResponse,
)
from backend.app.core.db.errors import DatabaseConflictError
from backend.app.core.db.session import get_db_session
from backend.app.core.pagination import PageParams
from backend.app.domains.access.admin import IdentityAdminService
from backend.app.domains.access.models import User
from backend.app.domains.workspace.tenants.models import WorkspaceMember
from backend.app.observability.audit.security_events import SecurityAuditService

router = APIRouter()


def _member_response(member: WorkspaceMember, user: User) -> AdminWorkspaceMemberResponse:
    return AdminWorkspaceMemberResponse(
        id=member.id,
        workspace_id=member.workspace_id,
        user_id=member.user_id,
        email=user.email,
        display_name=user.display_name,
        role=member.role,
        status=member.status,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


def _user_detail(
    service: IdentityAdminService,
    user: User,
) -> AdminUserDetailResponse:
    memberships = [
        _member_response(member, user)
        for member, _workspace in service.get_user_memberships(user.id)
    ]
    return AdminUserDetailResponse(
        id=user.id,
        created_at=user.created_at,
        updated_at=user.updated_at,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        platform_admin=user.platform_admin,
        workspace_memberships=memberships,
    )


@router.get("/users", response_model=PageResponse[AdminUserListResponse])
async def list_admin_users(
    page: PageParams = Depends(pagination_params),
    user_status: Literal["active", "disabled", "invited"] | None = Query(
        default=None, alias="status"
    ),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminUserListResponse]:
    service = IdentityAdminService(session)
    users, total = service.list_users(page, status=user_status)
    summaries = service.user_organization_summaries([user.id for user in users])
    return PageResponse(
        items=[
            AdminUserListResponse.model_validate(user).model_copy(update=summaries[user.id])
            for user in users
        ],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/users",
    response_model=AdminUserCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_user(
    payload: AdminUserCreateRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> AdminUserCreateResponse:
    initial_password = payload.password or token_urlsafe(32)
    service = IdentityAdminService(session)
    try:
        user = service.create_user(
            email=payload.email,
            display_name=payload.display_name,
            password=initial_password,
            username=payload.username,
            platform_admin=payload.platform_admin,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    SecurityAuditService(session).record_request_event(
        request_context=security_request_context(request),
        action="identity.user_created",
        outcome="allowed",
        severity="info",
        reason="Platform administrator created a user",
        user_id=user.id,
        metadata={"platform_admin": user.platform_admin, "actor": "platform_admin"},
    )
    session.commit()
    session.refresh(user)
    return AdminUserCreateResponse(
        id=user.id,
        created_at=user.created_at,
        updated_at=user.updated_at,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        platform_admin=user.platform_admin,
        initial_password=initial_password,
    )


@router.get("/users/{user_id}", response_model=AdminUserDetailResponse)
async def get_admin_user(
    user_id: UUID,
    session: Session = Depends(get_db_session),
) -> AdminUserDetailResponse:
    service = IdentityAdminService(session)
    user = service.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _user_detail(service, user)


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
async def update_admin_user(
    user_id: UUID,
    payload: AdminUserUpdateRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> AdminUserResponse:
    service = IdentityAdminService(session)
    try:
        user = service.update_user(
            user_id,
            display_name=payload.display_name,
            username=payload.username,
            platform_admin=payload.platform_admin,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    SecurityAuditService(session).record_request_event(
        request_context=security_request_context(request),
        action="identity.user_updated",
        outcome="allowed",
        severity="warning" if payload.platform_admin is not None else "info",
        reason="Platform administrator updated user attributes",
        user_id=user.id,
        metadata={
            "fields": sorted(payload.model_fields_set),
            "platform_admin": user.platform_admin,
            "actor": "platform_admin",
        },
    )
    session.commit()
    return AdminUserResponse.model_validate(user)


@router.post(
    "/users/{user_id}/reset-password",
    response_model=AdminUserPasswordResetResponse,
)
async def reset_admin_user_password(
    user_id: UUID,
    request: Request,
    session: Session = Depends(get_db_session),
) -> AdminUserPasswordResetResponse:
    service = IdentityAdminService(session)
    result = service.reset_password(user_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user, temporary_password = result
    SecurityAuditService(session).record_request_event(
        request_context=security_request_context(request),
        action="identity.user_password_reset",
        outcome="allowed",
        severity="warning",
        reason="Platform administrator reset a user password",
        user_id=user.id,
        metadata={"tokens_revoked": True, "actor": "platform_admin"},
    )
    session.commit()
    return AdminUserPasswordResetResponse(
        id=user.id,
        created_at=user.created_at,
        updated_at=user.updated_at,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        platform_admin=user.platform_admin,
        temporary_password=temporary_password,
    )


@router.post(
    "/users/{user_id}/revoke-tokens",
    response_model=AdminUserTokenRevokeResponse,
)
async def revoke_admin_user_tokens(
    user_id: UUID,
    request: Request,
    session: Session = Depends(get_db_session),
) -> AdminUserTokenRevokeResponse:
    service = IdentityAdminService(session)
    revoked = service.revoke_tokens(user_id)
    if revoked is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    SecurityAuditService(session).record_request_event(
        request_context=security_request_context(request),
        action="identity.user_tokens_revoked",
        outcome="allowed",
        severity="warning",
        reason="Platform administrator revoked user tokens",
        user_id=user_id,
        metadata={"revoked": revoked, "actor": "platform_admin"},
    )
    session.commit()
    return AdminUserTokenRevokeResponse(revoked=revoked)


@router.put("/users/{user_id}/status", response_model=AdminUserResponse)
async def update_admin_user_status(
    user_id: UUID,
    payload: AdminUserStatusUpdateRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> AdminUserResponse:
    service = IdentityAdminService(session)
    try:
        user = service.set_user_status(user_id, status=payload.status)
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    SecurityAuditService(session).record_request_event(
        request_context=security_request_context(request),
        action="identity.user_status_changed",
        outcome="allowed",
        severity="warning" if payload.status == "disabled" else "info",
        reason=f"Platform administrator changed user status to {payload.status}",
        user_id=user.id,
        metadata={"status": payload.status, "actor": "platform_admin"},
    )
    session.commit()
    return AdminUserResponse.model_validate(user)
