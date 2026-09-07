from __future__ import annotations

import json
from hashlib import sha256
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.capabilities.catalog import (
    CapabilityPolicyScope,
    CapabilityTeamPolicy,
    CapabilityToolDescriptor,
    EffectiveCapabilityCatalogResponse,
    EffectiveCapabilityDenial,
    EffectiveCapabilityResource,
    EffectiveCapabilityTool,
)
from backend.app.capabilities.catalog_service import WorkspaceCapabilityCatalogService
from backend.app.capabilities.schema_validation import (
    reject_embedded_secrets,
    validate_partial_parameters,
)
from backend.app.core.errors import DomainError, NotFoundError
from backend.app.teams.models import AgentTeam, AgentTeamMember


class EffectiveCapabilityCatalogService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def build(
        self,
        *,
        workspace_id: UUID,
        agent_profile_id: UUID,
        team_id: UUID | None = None,
    ) -> EffectiveCapabilityCatalogResponse:
        profile = self._session.scalar(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id == agent_profile_id,
                AgentProfile.status == "active",
            )
        )
        if profile is None:
            raise NotFoundError("Active agent profile not found", code="agent_profile_not_found")
        team, member, _policy, scopes = self._team_context(
            workspace_id=workspace_id,
            agent_profile_id=agent_profile_id,
            team_id=team_id,
        )
        raw_catalog = WorkspaceCapabilityCatalogService(self._session).build(workspace_id)
        denied: list[EffectiveCapabilityDenial] = []
        requested_tools = _string_set(
            profile.tool_policy.get("allowed_tools", profile.tool_policy.get("mcp_tools", [])),
            kind="tool",
            key="agent.tool_policy.allowed_tools",
            denied=denied,
        )
        requested_resources = _uuid_set(
            profile.capabilities.get("resource_ids", []),
            key="agent.capabilities.resource_ids",
            denied=denied,
        )
        tool_parameters = _parameter_map(
            profile.tool_policy.get("tool_parameters", {}),
            kind="tool",
            denied=denied,
        )
        resource_parameters = _parameter_map(
            profile.capabilities.get("resource_parameters", {}),
            kind="resource",
            denied=denied,
        )
        tools_by_name: dict[str, list[CapabilityToolDescriptor]] = {}
        for descriptor in raw_catalog.tools:
            tools_by_name.setdefault(descriptor.name, []).append(descriptor)
        resources_by_id = {resource.id: resource for resource in raw_catalog.resources}

        effective_tools: list[EffectiveCapabilityTool] = []
        for tool_name in sorted(requested_tools):
            blocked_by = _blocked_scope(tool_name, scopes, resource=False)
            if blocked_by is not None:
                denied.append(
                    EffectiveCapabilityDenial(kind="tool", key=tool_name, reason=blocked_by)
                )
                continue
            matches = tools_by_name.get(tool_name, [])
            if len(matches) != 1:
                reason = "tool is unavailable" if not matches else "tool name is ambiguous"
                denied.append(EffectiveCapabilityDenial(kind="tool", key=tool_name, reason=reason))
                continue
            descriptor = matches[0]
            try:
                parameters, locked, provenance = _merge_parameters(
                    base={},
                    key=tool_name,
                    scopes=scopes,
                    agent_parameters=tool_parameters.get(tool_name, {}),
                    schema=descriptor.input_schema,
                    label=f"tool {tool_name}",
                )
            except ValueError as exc:
                denied.append(
                    EffectiveCapabilityDenial(kind="tool", key=tool_name, reason=str(exc))
                )
                continue
            effective_tools.append(
                EffectiveCapabilityTool(
                    descriptor=descriptor,
                    parameters=parameters,
                    locked_parameters=locked,
                    provenance=provenance,
                )
            )

        effective_resources: list[EffectiveCapabilityResource] = []
        for resource_id in sorted(requested_resources, key=str):
            blocked_by = _blocked_scope(resource_id, scopes, resource=True)
            if blocked_by is not None:
                denied.append(
                    EffectiveCapabilityDenial(
                        kind="resource",
                        key=str(resource_id),
                        reason=blocked_by,
                    )
                )
                continue
            resource = resources_by_id.get(resource_id)
            if resource is None:
                denied.append(
                    EffectiveCapabilityDenial(
                        kind="resource",
                        key=str(resource_id),
                        reason="resource is unavailable",
                    )
                )
                continue
            try:
                parameters, locked, provenance = _merge_parameters(
                    base=resource.default_parameters,
                    key=resource_id,
                    scopes=scopes,
                    agent_parameters=resource_parameters.get(str(resource_id), {}),
                    schema=resource.parameter_schema,
                    label=f"resource {resource_id}",
                )
            except ValueError as exc:
                denied.append(
                    EffectiveCapabilityDenial(
                        kind="resource",
                        key=str(resource_id),
                        reason=str(exc),
                    )
                )
                continue
            effective_resources.append(
                EffectiveCapabilityResource(
                    resource=resource,
                    parameters=parameters,
                    locked_parameters=locked,
                    provenance=provenance,
                )
            )

        available_resource_access = {
            (item.resource.resource_type, item.resource.access_mode)
            for item in effective_resources
        }
        executable_tools: list[EffectiveCapabilityTool] = []
        for item in effective_tools:
            required_type = item.descriptor.required_resource_type
            required_modes = set(item.descriptor.required_access_modes)
            if required_type is not None and not any(
                resource_type == required_type and access_mode in required_modes
                for resource_type, access_mode in available_resource_access
            ):
                denied.append(
                    EffectiveCapabilityDenial(
                        kind="tool",
                        key=item.descriptor.name,
                        reason=(
                            f"requires an authorized {required_type} resource with "
                            f"one of these access modes: {', '.join(sorted(required_modes))}"
                        ),
                    )
                )
                continue
            executable_tools.append(item)

        response = EffectiveCapabilityCatalogResponse(
            workspace_id=workspace_id,
            agent_profile_id=profile.id,
            agent_profile_version=profile.version,
            team_id=team.id if team is not None else None,
            team_policy_version=team.capability_policy_version if team is not None else None,
            team_member_id=member.id if member is not None else None,
            department=member.department if member is not None else None,
            tools=executable_tools,
            resources=effective_resources,
            denied=denied,
            fingerprint="",
        )
        response.fingerprint = effective_catalog_fingerprint(response)
        return response

    def _team_context(
        self,
        *,
        workspace_id: UUID,
        agent_profile_id: UUID,
        team_id: UUID | None,
    ) -> tuple[
        AgentTeam | None,
        AgentTeamMember | None,
        CapabilityTeamPolicy | None,
        list[tuple[str, CapabilityPolicyScope]],
    ]:
        if team_id is None:
            return None, None, None, []
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
                AgentTeam.status == "active",
            )
        )
        if team is None:
            raise NotFoundError("Active team not found", code="team_not_found")
        member = self._session.scalar(
            select(AgentTeamMember).where(
                AgentTeamMember.workspace_id == workspace_id,
                AgentTeamMember.agent_team_id == team.id,
                AgentTeamMember.agent_profile_id == agent_profile_id,
                AgentTeamMember.status == "active",
            )
        )
        if member is None and team.manager_agent_profile_id != agent_profile_id:
            raise DomainError(
                "Agent is not an active member or manager of this team",
                code="agent_team_membership_required",
                status_code=403,
            )
        try:
            policy = CapabilityTeamPolicy.model_validate(team.capability_policy or {})
        except ValidationError as exc:
            raise DomainError(
                "Stored team capability policy is invalid",
                code="capability_policy_invalid",
                status_code=422,
            ) from exc
        scopes: list[tuple[str, CapabilityPolicyScope]] = [("team", policy)]
        if member is not None and member.department in policy.departments:
            scopes.append(
                (
                    f"department:{member.department}",
                    policy.departments[member.department],
                )
            )
        return team, member, policy, scopes


def _blocked_scope(
    key: str | UUID,
    scopes: list[tuple[str, CapabilityPolicyScope]],
    *,
    resource: bool,
) -> str | None:
    for label, scope in scopes:
        allowed = scope.allowed_resource_ids if resource else scope.allowed_tools
        if allowed != "*" and key not in allowed:
            return f"not allowed by {label} capability policy"
    return None


def _merge_parameters(
    *,
    base: dict[str, object],
    key: str | UUID,
    scopes: list[tuple[str, CapabilityPolicyScope]],
    agent_parameters: dict[str, object],
    schema: dict[str, object],
    label: str,
) -> tuple[dict[str, object], list[str], list[str]]:
    merged = dict(base)
    locked: set[str] = set()
    provenance = ["resource_defaults"] if base else []
    for scope_label, scope in scopes:
        if isinstance(key, UUID):
            config = scope.resource_parameters.get(key)
        else:
            config = scope.tool_parameters.get(key)
        if config is None:
            continue
        _apply_parameter_layer(merged, locked, config.defaults, label=scope_label)
        locked.update(config.locked)
        provenance.append(scope_label)
    _apply_parameter_layer(merged, locked, agent_parameters, label="agent")
    if agent_parameters:
        provenance.append("agent")
    reject_embedded_secrets(merged, path=label)
    validate_partial_parameters(merged, schema, label=f"effective {label} parameters")
    return merged, sorted(locked), provenance


def _apply_parameter_layer(
    merged: dict[str, object],
    locked: set[str],
    values: dict[str, object],
    *,
    label: str,
) -> None:
    for field, value in values.items():
        if field in locked and merged.get(field) != value:
            raise ValueError(f"{label} overrides locked parameter {field}")
        merged[field] = value


def _parameter_map(
    raw: object,
    *,
    kind: str,
    denied: list[EffectiveCapabilityDenial],
) -> dict[str, dict[str, object]]:
    if not isinstance(raw, dict):
        denied.append(
            EffectiveCapabilityDenial(
                kind="policy",
                key=f"agent.{kind}_parameters",
                reason="parameter configuration must be an object",
            )
        )
        return {}
    result: dict[str, dict[str, object]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            denied.append(
                EffectiveCapabilityDenial(
                    kind="policy",
                    key=str(key),
                    reason=f"agent {kind} parameters must be objects",
                )
            )
            continue
        result[key] = dict(value)
    return result


def _string_set(
    raw: object,
    *,
    kind: str,
    key: str,
    denied: list[EffectiveCapabilityDenial],
) -> set[str]:
    if not isinstance(raw, list):
        denied.append(
            EffectiveCapabilityDenial(kind="policy", key=key, reason=f"{kind} list is invalid")
        )
        return set()
    result = {item for item in raw if isinstance(item, str) and item}
    if len(result) != len(raw):
        denied.append(
            EffectiveCapabilityDenial(
                kind="policy",
                key=key,
                reason=f"{kind} list contains invalid or duplicate entries",
            )
        )
    return result


def _uuid_set(
    raw: object,
    *,
    key: str,
    denied: list[EffectiveCapabilityDenial],
) -> set[UUID]:
    if not isinstance(raw, list):
        denied.append(
            EffectiveCapabilityDenial(kind="policy", key=key, reason="resource list is invalid")
        )
        return set()
    result: set[UUID] = set()
    for item in raw:
        try:
            result.add(UUID(str(item)))
        except (TypeError, ValueError):
            denied.append(
                EffectiveCapabilityDenial(
                    kind="resource",
                    key=str(item),
                    reason="resource ID is invalid",
                )
            )
    return result


def effective_catalog_fingerprint(
    catalog: EffectiveCapabilityCatalogResponse | dict[str, object],
) -> str:
    payload = (
        catalog.model_dump(mode="json", exclude={"fingerprint"})
        if isinstance(catalog, EffectiveCapabilityCatalogResponse)
        else {key: value for key, value in catalog.items() if key != "fingerprint"}
    )
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{sha256(serialized.encode('utf-8')).hexdigest()}"
