from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.agents import AgentProfileCreateRequest
from backend.app.api.schemas.capabilities.mcp_servers import (
    McpServerCreateRequest,
    McpToolAllowRequest,
)
from backend.app.core.typing import dict_or_empty, string_or_default
from backend.app.marketplace.models import MarketplaceListing, TalentListing
from backend.app.reviews.constants import (
    RESOURCE_STATUS_PENDING_APPROVAL,
    REVIEW_TYPE_AGENT_PROFILE,
    REVIEW_TYPE_MCP_SERVER,
    REVIEW_TYPE_PLUGIN,
    REVIEW_TYPE_SKILL,
)

AGENT_SNAPSHOT_METADATA_KEY = "agent_snapshot"
PRIVATE_DEFINITION_KEYS = frozenset(
    {
        "agent_profile_id",
        "credential_id",
        "credential_reference_id",
        "credential_reference_ids",
        "installed_skill_id",
        "installed_skill_ids",
        "mcp_credential_reference_id",
        "mcp_credential_reference_ids",
        "mcp_server_id",
        "mcp_server_ids",
        "model_provider_credential_id",
        "runtime_id",
        "runtime_space_id",
        "self_hosted_runtime_id",
        "skill_install_id",
        "source_agent_profile_id",
        "source_workspace_id",
        "workspace_id",
        "workspace_runtime_id",
        "workspace_skill_install_id",
    }
)


@dataclass(frozen=True)
class AgentDefinitionSnapshot:
    name: str
    role: str
    description: str
    instructions: str
    model: str
    model_settings: dict[str, object]
    capabilities: dict[str, object]
    skills: dict[str, object]
    tool_policy: dict[str, object]
    runtime_policy: dict[str, object]
    memory_policy: dict[str, object]
    approval_policy: dict[str, object]


def agent_create_request_from_listing(
    listing: MarketplaceListing,
    config: dict[str, object],
) -> AgentProfileCreateRequest:
    manifest = dict(listing.manifest)
    payload = _mapping_from_manifest(manifest, "agent")
    override_name = config.get("agent_name") if isinstance(config, dict) else None
    payload.setdefault("name", override_name if isinstance(override_name, str) else listing.name)
    payload.setdefault("role", string_or_default(manifest.get("role"), "agent"))
    payload.setdefault("description", listing.summary)
    payload.setdefault("instructions", string_or_default(manifest.get("instructions"), ""))
    return AgentProfileCreateRequest.model_validate(payload)


def mcp_server_create_request_from_listing(listing: MarketplaceListing) -> McpServerCreateRequest:
    manifest = dict(listing.manifest)
    payload = _mapping_from_manifest(manifest, "mcp_server")
    payload.setdefault("name", listing.name)
    payload.setdefault("server_type", string_or_default(manifest.get("server_type"), "stdio"))
    connection = manifest.get("connection")
    payload.setdefault("connection", dict(connection) if isinstance(connection, dict) else {})
    payload["visibility"] = "private"
    return McpServerCreateRequest.model_validate(payload)


def mcp_tool_requests_from_listing(listing: MarketplaceListing) -> list[McpToolAllowRequest]:
    raw_tools = listing.manifest.get("tools") if isinstance(listing.manifest, dict) else None
    if not isinstance(raw_tools, list):
        return []
    requests: list[McpToolAllowRequest] = []
    for item in raw_tools:
        if isinstance(item, str):
            requests.append(McpToolAllowRequest(tool_name=item))
        elif isinstance(item, dict):
            requests.append(McpToolAllowRequest.model_validate(item))
    return requests


def listing_status(
    requested_status: str | None,
    visibility: str,
    review_required: bool,
) -> str:
    if review_required:
        return RESOURCE_STATUS_PENDING_APPROVAL
    if visibility == "public":
        return "public"
    if requested_status in {"draft", "archived"}:
        return requested_status
    return "active"


def listing_review_type(listing_type: str) -> str:
    if listing_type == "agent":
        return REVIEW_TYPE_AGENT_PROFILE
    if listing_type == "skill":
        return REVIEW_TYPE_SKILL
    if listing_type == "mcp_server":
        return REVIEW_TYPE_MCP_SERVER
    return REVIEW_TYPE_PLUGIN


def marketplace_source_checksum(listing: MarketplaceListing) -> str:
    material = f"{listing.id}:{listing.listing_type}:{listing.version}"
    return sha256(material.encode("utf-8")).hexdigest()


def listing_agent_definition(
    listing: TalentListing,
    source: AgentProfile,
) -> AgentDefinitionSnapshot:
    snapshot = listing.listing_metadata.get(AGENT_SNAPSHOT_METADATA_KEY)
    if isinstance(snapshot, dict):
        return AgentDefinitionSnapshot(
            name=_non_empty_string_or_default(snapshot.get("name"), source.name),
            role=_non_empty_string_or_default(snapshot.get("role"), source.role),
            description=_non_empty_string_or_default(
                snapshot.get("description"),
                source.description,
            ),
            instructions=_non_empty_string_or_default(
                snapshot.get("instructions"),
                source.instructions,
            ),
            model=_non_empty_string_or_default(snapshot.get("model"), source.model),
            model_settings=dict_or_empty(snapshot.get("model_settings")),
            capabilities=dict_or_empty(snapshot.get("capabilities")),
            skills=dict_or_empty(snapshot.get("skills")),
            tool_policy=dict_or_empty(snapshot.get("tool_policy")),
            runtime_policy=dict_or_empty(snapshot.get("runtime_policy")),
            memory_policy=dict_or_empty(snapshot.get("memory_policy")),
            approval_policy=dict_or_empty(snapshot.get("approval_policy")),
        )
    return agent_marketplace_snapshot(source)


def agent_marketplace_snapshot(agent: AgentProfile) -> AgentDefinitionSnapshot:
    return AgentDefinitionSnapshot(
        name=agent.name,
        role=agent.role,
        description=agent.description,
        instructions=agent.instructions,
        model=agent.model,
        model_settings=_safe_definition_dict(agent.model_settings),
        capabilities=_safe_definition_dict(agent.capabilities),
        skills=_safe_definition_dict(agent.skills),
        tool_policy=_safe_definition_dict(agent.tool_policy),
        runtime_policy=_safe_definition_dict(agent.runtime_policy),
        memory_policy=_safe_definition_dict(agent.memory_policy),
        approval_policy=_safe_definition_dict(agent.approval_policy),
    )


def _mapping_from_manifest(
    manifest: dict[str, object],
    key: str,
) -> dict[str, object]:
    nested = manifest.get(key)
    return dict(nested) if isinstance(nested, dict) else {}


def _safe_definition_dict(value: object) -> dict[str, object]:
    cleaned = _safe_definition_value(value)
    return cleaned if isinstance(cleaned, dict) else {}


def _safe_definition_value(value: object) -> object:
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, child in value.items():
            if key in PRIVATE_DEFINITION_KEYS or key.endswith("_id") or key.endswith("_ids"):
                continue
            cleaned[key] = _safe_definition_value(child)
        return cleaned
    if isinstance(value, list):
        return [_safe_definition_value(item) for item in value]
    return value


def _non_empty_string_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) else default
