"""Agent definitions are edited as data; execution resolves published revisions."""

from fastapi import APIRouter, Request
from pydantic import Field

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.agents.api import AgentDefinition

from ..security import CurrentUser, authorize, get_application, reject_secret_values

router = APIRouter(tags=["agents"])


class UpdateAgentRequest(OpsMeshContract):
    specification: AgentSpecification
    expected_revision: int = Field(ge=1)


class RevisionRequest(OpsMeshContract):
    expected_revision: int = Field(ge=1)


class AgentEnabledRequest(RevisionRequest):
    enabled: bool


class CloneAgentRequest(OpsMeshContract):
    target_id: str = Field(min_length=1)


def validate_http_specification(spec: AgentSpecification) -> None:
    reject_secret_values(spec.model_settings)
    if spec.sandbox is not None:
        sandbox = spec.sandbox
        if (
            sandbox.provider != "docker"
            or sandbox.trusted_local
            or sandbox.session_state
            or sandbox.snapshot_path
            or sandbox.manifest
            or sandbox.options
            or sandbox.run_as
        ):
            raise OpsMeshError(
                "CONFIG_INVALID",
                "HTTP sandbox configuration cannot introduce host paths, "
                "native state or provider options; use a deployed configuration",
            )
        if any(sandbox.capability_options):
            raise OpsMeshError(
                "CONFIG_INVALID", "Native capability options require a deployed extension"
            )


@router.post("/agents", status_code=201)
async def create_agent(
    body: AgentSpecification, request: Request, user: CurrentUser
) -> AgentDefinition:
    await authorize(request, user, "agent:create", body.id)
    validate_http_specification(body)
    return await get_application(request).services.agent_management.create(body, actor_id=user.id)


@router.get("/agents")
async def agents(request: Request, user: CurrentUser) -> list[AgentDefinition]:
    return await get_application(request).services.agent_management.list(actor_id=user.id)


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request, user: CurrentUser) -> AgentDefinition:
    agent = await get_application(request).services.agent_management.get(agent_id, actor_id=user.id)
    await authorize(request, user, "agent:read", agent.id, owner_id=agent.owner_id)
    return agent


@router.put("/agents/{agent_id}")
async def update_agent(
    agent_id: str, body: UpdateAgentRequest, request: Request, user: CurrentUser
) -> AgentDefinition:
    app = get_application(request)
    current = await app.services.agent_management.get(agent_id, actor_id=user.id)
    await authorize(request, user, "agent:write", agent_id, owner_id=current.owner_id)
    validate_http_specification(body.specification)
    return await app.services.agent_management.update(
        agent_id, body.specification, body.expected_revision, actor_id=user.id
    )


@router.post("/agents/{agent_id}/publish")
async def publish(
    agent_id: str, body: RevisionRequest, request: Request, user: CurrentUser
) -> AgentSpecification:
    app = get_application(request)
    current = await app.services.agent_management.get(agent_id, actor_id=user.id)
    await authorize(request, user, "agent:publish", agent_id, owner_id=current.owner_id)
    validate_http_specification(current.draft)
    return await app.services.agent_management.publish(
        agent_id, body.expected_revision, actor_id=user.id
    )


@router.put("/agents/{agent_id}/enabled")
async def set_enabled(
    agent_id: str, body: AgentEnabledRequest, request: Request, user: CurrentUser
) -> AgentDefinition:
    app = get_application(request)
    current = await app.services.agent_management.get(agent_id, actor_id=user.id)
    await authorize(request, user, "agent:write", agent_id, owner_id=current.owner_id)
    return await app.services.agent_management.set_enabled(
        agent_id, body.enabled, body.expected_revision, actor_id=user.id
    )


@router.get("/agent-revisions/{reference:path}")
async def revision(reference: str, request: Request, user: CurrentUser) -> AgentSpecification:
    app = get_application(request)
    agent = await app.services.agent_management.get_revision(reference, actor_id=user.id)
    definition = await app.services.agent_management.get(agent.id, actor_id=user.id)
    await authorize(request, user, "agent:read", agent.id, owner_id=definition.owner_id)
    return agent


@router.post("/agent-revisions/{reference:path}/clone", status_code=201)
async def clone(
    reference: str, body: CloneAgentRequest, request: Request, user: CurrentUser
) -> AgentDefinition:
    app = get_application(request)
    source = await app.services.agent_management.get_revision(reference, actor_id=user.id)
    definition = await app.services.agent_management.get(source.id, actor_id=user.id)
    await authorize(request, user, "agent:read", source.id, owner_id=definition.owner_id)
    await authorize(request, user, "agent:create", body.target_id)
    return await app.services.agent_management.clone(reference, body.target_id, actor_id=user.id)
