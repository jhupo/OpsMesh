from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations.providers import ModelProviderOperationsResponse
from backend.app.core.auth.context import WorkspaceContext
from backend.app.core.auth.dependencies import workspace_dependency
from backend.app.core.auth.permissions import WorkspaceAction
from backend.app.core.db.session import get_db_session
from backend.app.runtime.operations.model_providers import ModelProviderOperationsService

router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["operations"])


@router.get("/model-providers", response_model=ModelProviderOperationsResponse)
async def model_provider_operations(
    run_limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> ModelProviderOperationsResponse:
    return ModelProviderOperationsService(session).payload(
        context.workspace.id,
        run_limit=run_limit,
    )
