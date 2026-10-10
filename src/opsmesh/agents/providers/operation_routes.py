from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.runtime.operations.contracts.providers import ModelProviderOperationsResponse
from opsmesh.runtime.operations.model_providers import ModelProviderOperationsService
from opsmesh.shared.db.session import get_db_session

router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["operations"])


@router.get("/model-providers", response_model=ModelProviderOperationsResponse)
def model_provider_operations(
    run_limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> ModelProviderOperationsResponse:
    return ModelProviderOperationsService(session).payload(
        context.workspace.id,
        run_limit=run_limit,
    )
