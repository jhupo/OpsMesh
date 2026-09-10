from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.workspaces import (
    WorkspaceExecutionSlotSummaryResponse,
    WorkspaceQuotaResponse,
    WorkspaceQuotaUpsertRequest,
)
from backend.app.api.services.workspace_quotas import WorkspaceQuotaService
from backend.app.api.services.workspaces import WorkspaceService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.pagination import PageParams
from backend.app.db.session import get_db_session

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


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
    summary = WorkspaceQuotaService(session).execution_slot_summary(context.workspace.id)
    return WorkspaceExecutionSlotSummaryResponse.model_validate(summary)


@router.put("/{workspace_id}/quotas", response_model=list[WorkspaceQuotaResponse])
async def upsert_workspace_quotas(
    request: WorkspaceQuotaUpsertRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> list[WorkspaceQuotaResponse]:
    quotas = WorkspaceQuotaService(session).upsert_quotas(
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
    quota = WorkspaceQuotaService(session).disable_quota(
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
