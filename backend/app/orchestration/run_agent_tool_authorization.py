from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.core.contracts import (
    AgentRuntimeAgentDefinition,
    AgentRuntimeAgentRef,
    AgentRuntimeAgentTool,
    AgentRuntimeContext,
)
from backend.app.agents.models import AgentProfile
from backend.app.capabilities.effective_catalog import (
    EffectiveCapabilityCatalogService,
    effective_catalog_fingerprint,
)
from backend.app.capabilities.schema_validation import validate_partial_parameters
from backend.app.model_providers.provider_keys import (
    is_anthropic_provider,
    is_openai_compatible_provider,
)
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam, AgentTeamMember

from .run_request.authorization import (
    resource_grants_for_snapshot,
    tool_definitions_for_snapshot,
)
from .run_request.utils import dict_copy, string_list, uuid_or_none

MAX_AGENT_TOOL_DEPTH = 3
MAX_AGENT_TOOL_TURNS = 20
MAX_AGENT_TOOL_TARGETS = 10
MAX_AGENT_TOOL_GRAPH_NODES = 25


@dataclass(frozen=True, slots=True)
class AgentToolPolicy:
    allowed_profile_ids: tuple[UUID, ...] | None
    max_depth: int
    max_turns: int
    max_targets: int


@dataclass(slots=True)
class AgentToolAuthorizationSnapshotService:
    session: Session

    def build(
        self,
        *,
        task: Task,
        source_profile: AgentProfile | None,
        source_catalog: dict[str, object] | None,
        source_model_provider: dict[str, object],
        file_scope_ids: tuple[UUID, ...],
        model_provider_snapshot: Callable[[AgentProfile], dict[str, object]],
    ) -> list[dict[str, object]]:
        if (
            source_profile is None
            or source_profile.id is None
            or source_catalog is None
            or task.agent_team_id is None
        ):
            return []
        policy = _agent_tool_policy(source_profile)
        if policy is None:
            return []
        team = self.session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == task.workspace_id,
                AgentTeam.id == task.agent_team_id,
                AgentTeam.status == "active",
            )
        )
        if team is None:
            raise ValueError("Agent tool authorization requires an active task team")
        graph_size = [0]
        return self._build_level(
            task=task,
            team=team,
            source_profile=source_profile,
            source_catalog=source_catalog,
            source_model_provider=source_model_provider,
            policy=policy,
            depth=1,
            branch_max_depth=policy.max_depth,
            path=(source_profile.id,),
            file_scope_ids=file_scope_ids,
            model_provider_snapshot=model_provider_snapshot,
            graph_size=graph_size,
        )

    def _build_level(
        self,
        *,
        task: Task,
        team: AgentTeam,
        source_profile: AgentProfile,
        source_catalog: dict[str, object],
        source_model_provider: dict[str, object],
        policy: AgentToolPolicy,
        depth: int,
        branch_max_depth: int,
        path: tuple[UUID, ...],
        file_scope_ids: tuple[UUID, ...],
        model_provider_snapshot: Callable[[AgentProfile], dict[str, object]],
        graph_size: list[int],
    ) -> list[dict[str, object]]:
        candidates = self._team_candidates(team)
        target_ids = (
            sorted((item for item in candidates if item not in path), key=str)
            if policy.allowed_profile_ids is None
            else list(policy.allowed_profile_ids)
        )
        if len(target_ids) > policy.max_targets:
            raise ValueError("Agent tool policy exposes more targets than max_targets")
        snapshots: list[dict[str, object]] = []
        tool_names: set[str] = set()
        for target_id in target_ids:
            if target_id in path:
                continue
            target = candidates.get(target_id)
            if target is None:
                raise ValueError(
                    "Agent tool target must be an active, task-accepting member of the task team"
                )
            graph_size[0] += 1
            if graph_size[0] > MAX_AGENT_TOOL_GRAPH_NODES:
                raise ValueError("Agent tool graph exceeds the maximum authorized node count")
            target_provider = model_provider_snapshot(target)
            _require_same_provider_family(source_model_provider, target_provider)
            target_catalog = EffectiveCapabilityCatalogService(self.session).build(
                workspace_id=task.workspace_id,
                agent_profile_id=target.id,
                team_id=team.id,
            ).model_dump(mode="json")
            scoped_catalog = _intersect_catalog(source_catalog, target_catalog)
            tool_name = _agent_tool_name(target)
            if tool_name in tool_names:
                raise ValueError("Agent tool names must be unique")
            tool_names.add(tool_name)
            target_policy = _agent_tool_policy(target)
            nested: list[dict[str, object]] = []
            if target_policy is not None and depth < branch_max_depth:
                child_max_depth = min(
                    branch_max_depth,
                    depth + target_policy.max_depth,
                )
                nested = self._build_level(
                    task=task,
                    team=team,
                    source_profile=target,
                    source_catalog=scoped_catalog,
                    source_model_provider=target_provider,
                    policy=target_policy,
                    depth=depth + 1,
                    branch_max_depth=child_max_depth,
                    path=(*path, target.id),
                    file_scope_ids=file_scope_ids,
                    model_provider_snapshot=model_provider_snapshot,
                    graph_size=graph_size,
                )
            snapshots.append(
                {
                    "tool_name": tool_name,
                    "description": target.description
                    or f"Delegate focused work to {target.name} ({target.role}).",
                    "depth": depth,
                    "max_depth": branch_max_depth,
                    "max_turns": policy.max_turns,
                    "target": {
                        "workspace_id": str(task.workspace_id),
                        "profile_id": str(target.id),
                        "profile_version": target.version,
                        "name": target.name,
                        "role": target.role,
                        "instructions": target.instructions,
                        "model_settings": dict(target.model_settings or {}),
                        "handoff_description": target.description or None,
                    },
                    "model_provider": target_provider,
                    "capability_catalog": scoped_catalog,
                    "file_scope_ids": [str(item) for item in file_scope_ids],
                    "agent_tools": nested,
                }
            )
        return snapshots

    def _team_candidates(self, team: AgentTeam) -> dict[UUID, AgentProfile]:
        members = list(
            self.session.scalars(
                select(AgentTeamMember).where(
                    AgentTeamMember.workspace_id == team.workspace_id,
                    AgentTeamMember.agent_team_id == team.id,
                    AgentTeamMember.status == "active",
                    AgentTeamMember.accepts_tasks.is_(True),
                )
            )
        )
        candidate_ids = {item.agent_profile_id for item in members}
        if team.manager_agent_profile_id is not None:
            candidate_ids.add(team.manager_agent_profile_id)
        profiles = self.session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == team.workspace_id,
                AgentProfile.id.in_(candidate_ids),
                AgentProfile.status == "active",
            )
        ).all()
        return {item.id: item for item in profiles}


def hydrate_agent_tools(
    *,
    session: Session,
    snapshot: dict[str, object],
    root_context: AgentRuntimeContext,
    resolve_model_provider: Callable[..., dict[str, Any]],
) -> tuple[AgentRuntimeAgentTool, ...]:
    raw_items = snapshot.get("agent_tools")
    if raw_items is None:
        return ()
    if not isinstance(raw_items, list):
        raise ValueError("Authorization snapshot agent tools are invalid")
    return _hydrate_level(
        session=session,
        raw_items=raw_items,
        root_context=root_context,
        resolve_model_provider=resolve_model_provider,
        path=(),
    )


def _hydrate_level(
    *,
    session: Session,
    raw_items: list[object],
    root_context: AgentRuntimeContext,
    resolve_model_provider: Callable[..., dict[str, Any]],
    path: tuple[UUID, ...],
) -> tuple[AgentRuntimeAgentTool, ...]:
    hydrated: list[AgentRuntimeAgentTool] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ValueError("Authorization snapshot agent tool entry is invalid")
        target = dict_copy(raw_item.get("target"))
        profile_id = uuid_or_none(target.get("profile_id"))
        workspace_id = uuid_or_none(target.get("workspace_id"))
        if profile_id is None or workspace_id != root_context.workspace_id:
            raise ValueError("Authorization snapshot agent tool target is invalid")
        if profile_id in path:
            raise ValueError("Authorization snapshot agent tool graph contains a cycle")
        active_profile = session.scalar(
            select(AgentProfile.id).where(
                AgentProfile.workspace_id == root_context.workspace_id,
                AgentProfile.id == profile_id,
                AgentProfile.status == "active",
            )
        )
        if active_profile is None:
            raise ValueError("Authorized agent tool target is no longer active")
        provider_snapshot = dict_copy(raw_item.get("model_provider"))
        selected_model = provider_snapshot.get("selected_model")
        if not isinstance(selected_model, str) or not selected_model:
            raise ValueError("Authorization snapshot agent tool model is invalid")
        credential_id = uuid_or_none(provider_snapshot.get("credential_id"))
        model_api = provider_snapshot.get("model_api")
        resolved = resolve_model_provider(
            workspace_id=root_context.workspace_id,
            credential_id=credential_id,
            agent_model=selected_model,
            model_api=model_api if isinstance(model_api, str) else None,
            prefer_model_api=isinstance(model_api, str),
        )
        if (
            resolved.get("provider") != provider_snapshot.get("provider")
            or resolved.get("model_provider_credential_id") != credential_id
        ):
            raise ValueError("Agent tool model provider no longer matches the frozen snapshot")
        catalog = dict_copy(raw_item.get("capability_catalog"))
        fingerprint = catalog.get("fingerprint")
        if not isinstance(fingerprint, str) or fingerprint != effective_catalog_fingerprint(
            catalog
        ):
            raise ValueError("Agent tool capability catalog fingerprint mismatch")
        if uuid_or_none(catalog.get("workspace_id")) != root_context.workspace_id:
            raise ValueError("Agent tool capability catalog workspace mismatch")
        if uuid_or_none(catalog.get("agent_profile_id")) != profile_id:
            raise ValueError("Agent tool capability catalog profile mismatch")
        scoped_snapshot: dict[str, object] = {"capability_catalog": catalog}
        tool_definitions = tool_definitions_for_snapshot(scoped_snapshot)
        resource_grants = resource_grants_for_snapshot(scoped_snapshot)
        allowed_tools = tuple(item.name for item in tool_definitions)
        file_scope_ids = _uuid_list(raw_item.get("file_scope_ids"))
        if any(item not in root_context.file_scope_ids for item in file_scope_ids):
            raise ValueError("Agent tool file scope exceeds the parent run scope")
        depth = _bounded_int(raw_item.get("depth"), "depth", 1, MAX_AGENT_TOOL_DEPTH)
        max_depth = _bounded_int(
            raw_item.get("max_depth"),
            "max_depth",
            depth,
            MAX_AGENT_TOOL_DEPTH,
        )
        max_turns = _bounded_int(
            raw_item.get("max_turns"),
            "max_turns",
            1,
            MAX_AGENT_TOOL_TURNS,
        )
        tool_name = raw_item.get("tool_name")
        description = raw_item.get("description")
        name = target.get("name")
        role = target.get("role")
        instructions = target.get("instructions")
        model_settings = target.get("model_settings")
        if not isinstance(tool_name, str) or not tool_name:
            raise ValueError("Authorization snapshot agent tool name is invalid")
        if not isinstance(description, str) or not description:
            raise ValueError("Authorization snapshot agent tool description is invalid")
        if not isinstance(name, str) or not name:
            raise ValueError("Authorization snapshot agent tool target name is invalid")
        if not isinstance(role, str) or not role:
            raise ValueError("Authorization snapshot agent tool target role is invalid")
        if not isinstance(instructions, str) or not instructions:
            raise ValueError("Authorization snapshot agent tool instructions are invalid")
        if not isinstance(model_settings, dict):
            raise ValueError("Authorization snapshot agent tool definition is invalid")
        handoff_description = target.get("handoff_description")
        context = AgentRuntimeContext(
            workspace_id=root_context.workspace_id,
            task_id=root_context.task_id,
            run_id=root_context.run_id,
            user_id=root_context.user_id,
            allowed_tools=allowed_tools,
            tool_definitions=tool_definitions,
            resource_grants=resource_grants,
            file_scope_ids=file_scope_ids,
            runtime_binding=root_context.runtime_binding,
            metadata={
                **root_context.metadata,
                "agent_tool": {
                    "target_agent_profile_id": str(profile_id),
                    "depth": depth,
                    "max_depth": max_depth,
                    "capability_catalog_fingerprint": fingerprint,
                },
                "capability_catalog_fingerprint": fingerprint,
            },
        )
        raw_nested = raw_item.get("agent_tools", [])
        if not isinstance(raw_nested, list):
            raise ValueError("Authorization snapshot nested agent tools are invalid")
        nested = _hydrate_level(
            session=session,
            raw_items=raw_nested,
            root_context=root_context,
            resolve_model_provider=resolve_model_provider,
            path=(*path, profile_id),
        )
        hydrated.append(
            AgentRuntimeAgentTool(
                target=AgentRuntimeAgentDefinition(
                    ref=AgentRuntimeAgentRef(
                        name=name,
                        profile_id=profile_id,
                        role=role,
                    ),
                    workspace_id=root_context.workspace_id,
                    instructions=instructions,
                    model=selected_model,
                    model_settings=dict(model_settings),
                    handoff_description=handoff_description
                    if isinstance(handoff_description, str)
                    else None,
                ),
                tool_name=tool_name,
                description=description,
                context=context,
                max_turns=max_turns,
                depth=depth,
                max_depth=max_depth,
                model=str(resolved["model"]),
                provider=str(resolved["provider"]),
                base_url=resolved.get("base_url"),
                api_key=resolved.get("api_key"),
                model_api=resolved.get("model_api"),
                model_provider_credential_id=resolved.get("model_provider_credential_id"),
                nested_tools=nested,
            )
        )
    return tuple(hydrated)


def _agent_tool_policy(profile: AgentProfile) -> AgentToolPolicy | None:
    raw = profile.tool_policy.get("agent_tools") if isinstance(profile.tool_policy, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Agent tool policy must be an object")
    if raw.get("enabled") is not True:
        return None
    raw_allowed = raw.get("allowed_agent_profile_ids")
    allowed_profile_ids: tuple[UUID, ...] | None
    if raw_allowed == "*":
        allowed_profile_ids = None
    elif isinstance(raw_allowed, list):
        parsed = tuple(uuid_or_none(item) for item in raw_allowed)
        if any(item is None for item in parsed) or len(set(parsed)) != len(parsed):
            raise ValueError("Agent tool allowed profile IDs are invalid or duplicated")
        allowed_profile_ids = tuple(item for item in parsed if item is not None)
    else:
        raise ValueError("Agent tool policy requires allowed_agent_profile_ids")
    return AgentToolPolicy(
        allowed_profile_ids=allowed_profile_ids,
        max_depth=_bounded_int(raw.get("max_depth", 1), "max_depth", 1, MAX_AGENT_TOOL_DEPTH),
        max_turns=_bounded_int(raw.get("max_turns", 8), "max_turns", 1, MAX_AGENT_TOOL_TURNS),
        max_targets=_bounded_int(
            raw.get("max_targets", 5),
            "max_targets",
            1,
            MAX_AGENT_TOOL_TARGETS,
        ),
    )


def _intersect_catalog(
    parent: dict[str, object],
    target: dict[str, object],
) -> dict[str, object]:
    scoped = deepcopy(target)
    parent_tools = {
        _descriptor_key(item): item
        for item in _object_list(parent.get("tools"), "parent tools")
    }
    tools: list[dict[str, object]] = []
    for target_item in _object_list(target.get("tools"), "target tools"):
        parent_item = parent_tools.get(_descriptor_key(target_item))
        if parent_item is None:
            continue
        if parent_item.get("descriptor") != target_item.get("descriptor"):
            raise ValueError("Agent tool descriptor differs from its parent authorization scope")
        merged = _merge_policy_item(parent_item, target_item)
        descriptor = dict_copy(merged.get("descriptor"))
        validate_partial_parameters(
            dict_copy(merged.get("parameters")),
            dict_copy(descriptor.get("input_schema")),
            label=f"agent tool {descriptor.get('name')}",
        )
        tools.append(merged)
    parent_resources = {
        _resource_key(item): item
        for item in _object_list(parent.get("resources"), "parent resources")
    }
    resources: list[dict[str, object]] = []
    for target_item in _object_list(target.get("resources"), "target resources"):
        parent_item = parent_resources.get(_resource_key(target_item))
        if parent_item is None:
            continue
        if parent_item.get("resource") != target_item.get("resource"):
            raise ValueError("Agent resource differs from its parent authorization scope")
        resources.append(_merge_policy_item(parent_item, target_item))
    available_access = {
        (
            dict_copy(item.get("resource")).get("resource_type"),
            dict_copy(item.get("resource")).get("access_mode"),
        )
        for item in resources
    }
    tools = [
        item
        for item in tools
        if _tool_has_required_resource(dict_copy(item.get("descriptor")), available_access)
    ]
    scoped["tools"] = tools
    scoped["resources"] = resources
    scoped["denied"] = []
    scoped["fingerprint"] = effective_catalog_fingerprint(scoped)
    return scoped


def _merge_policy_item(
    parent: dict[str, object],
    target: dict[str, object],
) -> dict[str, object]:
    merged = deepcopy(target)
    parent_parameters = dict_copy(parent.get("parameters"))
    target_parameters = dict_copy(target.get("parameters"))
    parent_locked = set(string_list(parent.get("locked_parameters")))
    target_locked = set(string_list(target.get("locked_parameters")))
    for field in parent_locked & target_locked:
        if parent_parameters.get(field) != target_parameters.get(field):
            raise ValueError(f"Agent tool scope has conflicting locked parameter {field}")
    for field in parent_locked:
        target_parameters[field] = parent_parameters.get(field)
    merged["parameters"] = target_parameters
    merged["locked_parameters"] = sorted(parent_locked | target_locked)
    merged["provenance"] = sorted(
        set(string_list(parent.get("provenance")))
        | set(string_list(target.get("provenance")))
        | {"parent_agent_scope"}
    )
    return merged


def _tool_has_required_resource(
    descriptor: dict[str, object],
    available_access: set[tuple[object, object]],
) -> bool:
    resource_type = descriptor.get("required_resource_type")
    if resource_type is None:
        return True
    modes = set(string_list(descriptor.get("required_access_modes")))
    return any(
        item_type == resource_type and mode in modes
        for item_type, mode in available_access
    )


def _descriptor_key(item: dict[str, object]) -> str:
    descriptor = dict_copy(item.get("descriptor"))
    name = descriptor.get("name")
    return name if isinstance(name, str) else ""


def _resource_key(item: dict[str, object]) -> str:
    resource = dict_copy(item.get("resource"))
    resource_id = resource.get("id")
    return str(resource_id) if resource_id is not None else ""


def _object_list(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"Capability catalog {label} are invalid")
    return [dict(item) for item in value]


def _uuid_list(value: object) -> tuple[UUID, ...]:
    if not isinstance(value, list):
        raise ValueError("Agent tool file scope is invalid")
    parsed = tuple(uuid_or_none(item) for item in value)
    if any(item is None for item in parsed) or len(set(parsed)) != len(parsed):
        raise ValueError("Agent tool file scope contains invalid or duplicate IDs")
    return tuple(item for item in parsed if item is not None)


def _bounded_int(value: object, name: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"Agent tool {name} must be between {minimum} and {maximum}")
    return value


def _agent_tool_name(profile: AgentProfile) -> str:
    safe_name = "".join(char.lower() if char.isalnum() else "_" for char in profile.name)
    safe_name = "_".join(part for part in safe_name.split("_") if part)[:32] or "specialist"
    return f"delegate_to_{safe_name}_{profile.id.hex[:8]}"


def _require_same_provider_family(
    source: dict[str, object],
    target: dict[str, object],
) -> None:
    raw_source_provider = source.get("provider")
    raw_target_provider = target.get("provider")
    source_provider = raw_source_provider if isinstance(raw_source_provider, str) else None
    target_provider = raw_target_provider if isinstance(raw_target_provider, str) else None
    same_openai_family = is_openai_compatible_provider(source_provider) and (
        is_openai_compatible_provider(target_provider)
    )
    same_anthropic_family = is_anthropic_provider(source_provider) and is_anthropic_provider(
        target_provider
    )
    if not same_openai_family and not same_anthropic_family:
        raise ValueError("Agent tools cannot cross model-provider runtime families")
