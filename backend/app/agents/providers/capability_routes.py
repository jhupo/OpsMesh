from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.execution.contracts import AgentRuntimeCapability
from backend.app.agents.execution.dependencies import get_agent_runtime_registry
from backend.app.agents.execution.registry import ProviderAgentRuntimeRegistry
from backend.app.agents.providers.capabilities import list_model_capabilities
from backend.app.agents.providers.model_api import default_model_api, model_api_options_for_provider
from backend.app.agents.providers.models import ModelProviderCredential
from backend.app.agents.providers.schemas import (
    AgentRuntimeAdapterCapabilityResponse,
    AgentRuntimeCapabilityFeatureResponse,
    ModelCapabilityResponse,
)
from backend.app.identity.auth.dependencies import workspace_dependency
from backend.app.identity.authorization.context import WorkspaceContext
from backend.app.identity.authorization.permissions import WorkspaceAction
from backend.app.shared.db.session import get_db_session

router = APIRouter(
    prefix="/workspaces/{workspace_id}/model-provider-capabilities",
    tags=["model-providers"],
)


@router.get(
    "/agent-runtimes",
    response_model=list[AgentRuntimeAdapterCapabilityResponse],
)
def list_agent_runtime_adapter_capabilities(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    registry: ProviderAgentRuntimeRegistry = Depends(get_agent_runtime_registry),
) -> list[AgentRuntimeAdapterCapabilityResponse]:
    del context
    matrix = registry.capability_matrix()
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
def list_workspace_model_provider_capabilities(
    provider: str | None = Query(default=None),
    capability: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
) -> list[ModelCapabilityResponse]:
    return [
        ModelCapabilityResponse(
            **item.as_dict(),
            model_apis=list(model_api_options_for_provider(item.provider)),
            default_model_api=default_model_api(item.provider),
        )
        for credential in session.scalars(
            select(ModelProviderCredential).where(
                ModelProviderCredential.workspace_id == context.workspace.id,
                ModelProviderCredential.status == "active",
            )
        )
        for item in list_model_capabilities(
            credential.model_capabilities, provider=provider, capability=capability
        )
    ]
