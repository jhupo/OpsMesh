from fastapi import APIRouter, Depends, Query

from backend.app.agent_runtime.contracts import AgentRuntimeCapability
from backend.app.agent_runtime.factory import build_agent_runtime_registry
from backend.app.api.schemas.model_providers import (
    AgentRuntimeAdapterCapabilityResponse,
    AgentRuntimeCapabilityFeatureResponse,
    ModelCapabilityResponse,
)
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


@router.get(
    "/agent-runtimes",
    response_model=list[AgentRuntimeAdapterCapabilityResponse],
)
async def list_agent_runtime_adapter_capabilities(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
) -> list[AgentRuntimeAdapterCapabilityResponse]:
    del context
    matrix = build_agent_runtime_registry().capability_matrix()
    return [
        AgentRuntimeAdapterCapabilityResponse(
            provider=capabilities.provider,
            adapter=capabilities.adapter,
            limits=capabilities.limits,
            features=[
                AgentRuntimeCapabilityFeatureResponse(
                    name=feature.value,
                    supported=capabilities.supports(feature),
                    reason=(
                        None
                        if capabilities.supports(feature)
                        else capabilities.unsupported_reasons.get(
                            feature.value,
                            "The adapter does not advertise this capability.",
                        )
                    ),
                )
                for feature in AgentRuntimeCapability
            ],
        )
        for capabilities in matrix.values()
    ]


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
