from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.workspaces import (
    WorkspaceInviteAcceptRequest,
    WorkspaceInviteAcceptResponse,
    WorkspaceInviteCreateRequest,
    WorkspaceInviteCreateResponse,
    WorkspaceInviteResponse,
    WorkspaceMemberResponse,
)
from backend.app.api.services.workspace_errors import (
    WorkspaceInviteConflictError,
    WorkspaceInviteNotFoundError,
    WorkspaceInvitePermissionError,
    WorkspaceMemberPermissionError,
)
from backend.app.api.services.workspace_invites import (
    WorkspaceInviteService,
    fingerprint_invite_token,
)
from backend.app.api.services.workspaces import WorkspaceService
from backend.app.auth.context import AuthenticatedUser, WorkspaceContext
from backend.app.auth.dependencies import get_current_user, workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.security.service import SecurityAuditService

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("/invites/accept", response_model=WorkspaceInviteAcceptResponse)
async def accept_workspace_invite(
    request: WorkspaceInviteAcceptRequest,
    http_request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceInviteAcceptResponse:
    service = WorkspaceInviteService(session, settings)
    fingerprint = fingerprint_invite_token(request.token)
    try:
        accepted = service.accept_invite(request, actor_user_id=current_user.user_id)
    except WorkspaceInvitePermissionError as exc:
        record_invite_rejection(session, http_request, current_user, exc.message, fingerprint, exc)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message) from exc
    except WorkspaceInviteNotFoundError as exc:
        record_invite_rejection(session, http_request, current_user, exc.message, fingerprint)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except WorkspaceInviteConflictError as exc:
        record_invite_rejection(session, http_request, current_user, exc.message, fingerprint, exc)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc

    SecurityAuditService(session).record_request_event(
        request=http_request,
        action="workspace.invite.accepted",
        outcome="allowed",
        severity="info",
        reason="Workspace invite accepted",
        workspace_id=accepted.invite.workspace_id,
        user_id=current_user.user_id,
        metadata={"fingerprint": accepted.invite.fingerprint},
    )
    session.commit()
    return WorkspaceInviteAcceptResponse(
        invite=WorkspaceInviteResponse.model_validate(accepted.invite),
        member=WorkspaceMemberResponse.model_validate(accepted.member),
    )


@router.get("/{workspace_id}/invites", response_model=PageResponse[WorkspaceInviteResponse])
async def list_workspace_invites(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_MEMBERS)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceInviteResponse]:
    items, total = WorkspaceService(session).list_invites(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/{workspace_id}/invites",
    response_model=WorkspaceInviteCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_invite(
    request: WorkspaceInviteCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_MEMBERS)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceInviteCreateResponse:
    try:
        created = WorkspaceInviteService(session, settings).create_invite(
            context.workspace.id,
            request,
            actor_user_id=context.user.user_id,
            actor_role=context.role.value,
        )
    except WorkspaceInviteNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except WorkspaceMemberPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message) from exc
    except (WorkspaceInviteConflictError, DatabaseConflictError) as exc:
        message = exc.message if hasattr(exc, "message") else str(exc)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message) from exc
    payload = WorkspaceInviteResponse.model_validate(created.invite).model_dump()
    return WorkspaceInviteCreateResponse(**payload, token=created.token)


@router.delete("/{workspace_id}/invites/{invite_id}", response_model=WorkspaceInviteResponse)
async def revoke_workspace_invite(
    invite_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_MEMBERS)),
    session: Session = Depends(get_db_session),
) -> WorkspaceInviteResponse:
    try:
        invite = WorkspaceInviteService(session).revoke_invite(
            context.workspace.id,
            invite_id,
            actor_user_id=context.user.user_id,
        )
    except WorkspaceInviteNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except WorkspaceInviteConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return WorkspaceInviteResponse.model_validate(invite)


def record_invite_rejection(
    session: Session,
    request: Request,
    current_user: AuthenticatedUser,
    reason: str,
    fallback_fingerprint: str,
    exc: WorkspaceInvitePermissionError | WorkspaceInviteConflictError | None = None,
) -> None:
    SecurityAuditService(session).record_request_event(
        request=request,
        action="workspace.invite.accept_rejected",
        outcome="denied",
        severity="warning",
        reason=reason,
        workspace_id=exc.workspace_id if exc is not None else None,
        user_id=current_user.user_id,
        metadata={
            "fingerprint": (exc.fingerprint if exc is not None else None) or fallback_fingerprint
        },
    )
    session.commit()
