from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.idempotency import (
    IdempotencyInProgressError,
    IdempotencyService,
    run_idempotent_create,
)
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.workspaces import (
    WorkspaceCreateRequest,
    WorkspaceExecutionSlotSummaryResponse,
    WorkspaceInviteAcceptRequest,
    WorkspaceInviteAcceptResponse,
    WorkspaceInviteCreateRequest,
    WorkspaceInviteCreateResponse,
    WorkspaceInviteResponse,
    WorkspaceMemberCreateRequest,
    WorkspaceMemberResponse,
    WorkspaceMemberUpdateRequest,
    WorkspaceQuotaResponse,
    WorkspaceQuotaUpsertRequest,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)
from backend.app.api.services.workspaces import (
    WorkspaceInviteConflictError,
    WorkspaceInviteNotFoundError,
    WorkspaceInvitePermissionError,
    WorkspaceMemberConflictError,
    WorkspaceMemberNotFoundError,
    WorkspaceMemberPermissionError,
    WorkspaceService,
)
from backend.app.auth.context import AuthenticatedUser, WorkspaceContext
from backend.app.auth.dependencies import get_current_user, workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.security.service import SecurityAuditService

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=PageResponse[WorkspaceResponse])
async def list_workspaces(
    page: PageParams = Depends(pagination_params),
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceResponse]:
    items, total = WorkspaceService(session).list_for_user(current_user.user_id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    request: WorkspaceCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> WorkspaceResponse:
    service = WorkspaceService(session)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        workspace = run_idempotent_create(
            idempotency=idempotency,
            scope_id=current_user.user_id,
            operation="workspaces.create",
            idempotency_key=idempotency_key,
            get_existing=lambda workspace_id: service.get_owned(
                current_user.user_id,
                workspace_id,
            ),
            create=lambda: service.create_for_owner(current_user.user_id, request),
            resource_id=lambda created_workspace: created_workspace.id,
        )
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this Idempotency-Key is still processing",
        ) from exc
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return WorkspaceResponse.model_validate(workspace)


@router.post("/invites/accept", response_model=WorkspaceInviteAcceptResponse)
async def accept_workspace_invite(
    request: WorkspaceInviteAcceptRequest,
    http_request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceInviteAcceptResponse:
    service = WorkspaceService(session, settings)
    fingerprint = WorkspaceService.fingerprint_invite_token(request.token)
    try:
        accepted = service.accept_invite(request, actor_user_id=current_user.user_id)
    except WorkspaceInvitePermissionError as exc:
        SecurityAuditService(session).record_request_event(
            request=http_request,
            action="workspace.invite.accept_rejected",
            outcome="denied",
            severity="warning",
            reason=exc.message,
            workspace_id=exc.workspace_id,
            user_id=current_user.user_id,
            metadata={"fingerprint": exc.fingerprint or fingerprint},
        )
        session.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message) from exc
    except WorkspaceInviteNotFoundError as exc:
        SecurityAuditService(session).record_request_event(
            request=http_request,
            action="workspace.invite.accept_rejected",
            outcome="denied",
            severity="warning",
            reason=exc.message,
            user_id=current_user.user_id,
            metadata={"fingerprint": fingerprint},
        )
        session.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except WorkspaceInviteConflictError as exc:
        SecurityAuditService(session).record_request_event(
            request=http_request,
            action="workspace.invite.accept_rejected",
            outcome="denied",
            severity="warning",
            reason=exc.message,
            workspace_id=exc.workspace_id,
            user_id=current_user.user_id,
            metadata={"fingerprint": exc.fingerprint or fingerprint},
        )
        session.commit()
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


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
) -> WorkspaceResponse:
    return WorkspaceResponse.model_validate(context.workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    request: WorkspaceUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> WorkspaceResponse:
    workspace = WorkspaceService(session).get_scoped(context.workspace.id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return WorkspaceResponse.model_validate(
        WorkspaceService(session).update(
            workspace,
            request,
            actor_user_id=context.user.user_id,
        )
    )


@router.get("/{workspace_id}/members", response_model=PageResponse[WorkspaceMemberResponse])
async def list_workspace_members(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_MEMBERS)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceMemberResponse]:
    items, total = WorkspaceService(session).list_members(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


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
        created = WorkspaceService(session, settings).create_invite(
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
        invite = WorkspaceService(session).revoke_invite(
            context.workspace.id,
            invite_id,
            actor_user_id=context.user.user_id,
        )
    except WorkspaceInviteNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except WorkspaceInviteConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return WorkspaceInviteResponse.model_validate(invite)


@router.post(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_member(
    request: WorkspaceMemberCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_MEMBERS)),
    session: Session = Depends(get_db_session),
) -> WorkspaceMemberResponse:
    try:
        member = WorkspaceService(session).create_member(
            context.workspace.id,
            request,
            actor_user_id=context.user.user_id,
            actor_role=context.role.value,
        )
    except WorkspaceMemberNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except WorkspaceMemberPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message) from exc
    except WorkspaceMemberConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return WorkspaceMemberResponse.model_validate(member)


@router.patch(
    "/{workspace_id}/members/{member_id}",
    response_model=WorkspaceMemberResponse,
)
async def update_workspace_member(
    member_id: UUID,
    request: WorkspaceMemberUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_MEMBERS)),
    session: Session = Depends(get_db_session),
) -> WorkspaceMemberResponse:
    try:
        member = WorkspaceService(session).update_member(
            context.workspace.id,
            member_id,
            request,
            actor_user_id=context.user.user_id,
            actor_role=context.role.value,
        )
    except WorkspaceMemberNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except WorkspaceMemberPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message) from exc
    except WorkspaceMemberConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return WorkspaceMemberResponse.model_validate(member)


@router.delete(
    "/{workspace_id}/members/{member_id}",
    response_model=WorkspaceMemberResponse,
)
async def disable_workspace_member(
    member_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_MEMBERS)),
    session: Session = Depends(get_db_session),
) -> WorkspaceMemberResponse:
    try:
        member = WorkspaceService(session).disable_member(
            context.workspace.id,
            member_id,
            actor_user_id=context.user.user_id,
            actor_role=context.role.value,
        )
    except WorkspaceMemberNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except WorkspaceMemberPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message) from exc
    except WorkspaceMemberConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return WorkspaceMemberResponse.model_validate(member)


@router.get("/{workspace_id}/quotas", response_model=PageResponse[WorkspaceQuotaResponse])
async def list_workspace_quotas(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceQuotaResponse]:
    items, total = WorkspaceService(session).list_quotas(context.workspace.id, page)
    return PageResponse(
        items=[WorkspaceQuotaResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{workspace_id}/quotas/execution-summary",
    response_model=WorkspaceExecutionSlotSummaryResponse,
)
async def get_workspace_execution_slot_summary(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> WorkspaceExecutionSlotSummaryResponse:
    summary = WorkspaceService(session).execution_slot_summary(context.workspace.id)
    return WorkspaceExecutionSlotSummaryResponse.model_validate(summary)


@router.put("/{workspace_id}/quotas", response_model=list[WorkspaceQuotaResponse])
async def upsert_workspace_quotas(
    request: WorkspaceQuotaUpsertRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> list[WorkspaceQuotaResponse]:
    quotas = WorkspaceService(session).upsert_quotas(
        context.workspace.id,
        request,
        actor_user_id=context.user.user_id,
    )
    return [WorkspaceQuotaResponse.model_validate(quota) for quota in quotas]


@router.delete("/{workspace_id}/quotas/{quota_key}", response_model=WorkspaceQuotaResponse)
async def disable_workspace_quota(
    quota_key: str,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> WorkspaceQuotaResponse:
    quota = WorkspaceService(session).disable_quota(
        context.workspace.id,
        quota_key,
        actor_user_id=context.user.user_id,
    )
    if quota is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace quota not found",
        )
    return WorkspaceQuotaResponse.model_validate(quota)
