from fastapi import APIRouter, Depends, Query

from backend.app.api.schemas.model_providers import ModelCapabilityResponse
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.model_providers.capabilities import list_model_capabilities
from backend.app.model_providers.model_api import (
    default_model_api,
    model_api_options_for_provider,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/model-provider-capabilities",
    tags=["model-providers"],
)


@router.get("", response_model=list[ModelCapabilityResponse])
async def list_workspace_model_provider_capabilities(
    provider: str | None = Query(default=None),
    capability: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
) -> list[ModelCapabilityResponse]:
    del context
    return [
        ModelCapabilityResponse(
            **item.as_dict(),
            model_apis=list(model_api_options_for_provider(item.provider)),
            default_model_api=default_model_api(item.provider),
        )
        for item in list_model_capabilities(provider=provider, capability=capability)
    ]
