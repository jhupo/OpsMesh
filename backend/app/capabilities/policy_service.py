from __future__ import annotations

from typing import TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.capabilities.catalog import (
    CapabilityParameterPolicy,
    CapabilityPolicyScope,
    CapabilityTeamPolicy,
    CapabilityToolDescriptor,
)
from backend.app.observability.audit_service import AuditService
from backend.app.capabilities.catalog_service import WorkspaceCapabilityCatalogService
from backend.app.capabilities.schema_validation import validate_partial_parameters
from backend.app.core.errors import DomainError, NotFoundError
from backend.app.teams.models import AgentTeam

PolicyKey = TypeVar("PolicyKey", str, UUID)


class TeamCapabilityPolicyService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def update_policy(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        policy: CapabilityTeamPolicy,
        actor_user_id: UUID,
    ) -> AgentTeam:
        team = self._session.scalar(
            select(AgentTeam)
            .where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
            .with_for_update()
        )
        if team is None:
            raise NotFoundError("Team not found", code="team_not_found")
        self.validate_policy(workspace_id, policy)
        before = dict(team.capability_policy or {})
        team.capability_policy = policy.model_dump(mode="json")
        team.capability_policy_version += 1
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="team.capability_policy_updated",
            target_type="agent_team",
            target_id=team.id,
            metadata={
                "before": before,
                "after": team.capability_policy,
                "capability_policy_version": team.capability_policy_version,
            },
        )
        self._session.commit()
        self._session.refresh(team)
        return team

    def validate_policy(self, workspace_id: UUID, policy: CapabilityTeamPolicy) -> None:
        catalog = WorkspaceCapabilityCatalogService(self._session).build(workspace_id)
        tools_by_name: dict[str, list[CapabilityToolDescriptor]] = {}
        for descriptor in catalog.tools:
            tools_by_name.setdefault(descriptor.name, []).append(descriptor)
        resources_by_id = {resource.id: resource for resource in catalog.resources}
        scopes: list[tuple[str, CapabilityPolicyScope]] = [("team", policy)]
        scopes.extend(
            (f"department {department}", scope) for department, scope in policy.departments.items()
        )
        try:
            for label, scope in scopes:
                if scope.allowed_tools != "*":
                    for tool_name in scope.allowed_tools:
                        matches = tools_by_name.get(tool_name, [])
                        if not matches:
                            raise ValueError(f"{label} references unavailable tool {tool_name}")
                        if len(matches) != 1:
                            raise ValueError(f"{label} references ambiguous tool {tool_name}")
                if scope.allowed_resource_ids != "*":
                    for resource_id in scope.allowed_resource_ids:
                        if resource_id not in resources_by_id:
                            raise ValueError(
                                f"{label} references unavailable resource {resource_id}"
                            )
                for tool_name, parameters in scope.tool_parameters.items():
                    matches = tools_by_name.get(tool_name, [])
                    if len(matches) != 1:
                        qualifier = "unavailable" if not matches else "ambiguous"
                        raise ValueError(f"{label} configures {qualifier} tool {tool_name}")
                    validate_partial_parameters(
                        parameters.defaults,
                        matches[0].input_schema,
                        label=f"{label} tool parameters for {tool_name}",
                    )
                for resource_id, parameters in scope.resource_parameters.items():
                    resource = resources_by_id.get(resource_id)
                    if resource is None:
                        raise ValueError(f"{label} configures unavailable resource {resource_id}")
                    validate_partial_parameters(
                        parameters.defaults,
                        resource.parameter_schema,
                        label=f"{label} resource parameters for {resource_id}",
                    )
            _validate_department_lock_overrides(policy)
        except ValueError as exc:
            raise DomainError(
                str(exc),
                code="capability_policy_invalid",
                status_code=422,
            ) from exc


def _validate_department_lock_overrides(policy: CapabilityTeamPolicy) -> None:
    for department, scope in policy.departments.items():
        _reject_locked_overrides(
            policy.tool_parameters,
            scope.tool_parameters,
            label=f"department {department} tool",
        )
        _reject_locked_overrides(
            policy.resource_parameters,
            scope.resource_parameters,
            label=f"department {department} resource",
        )


def _reject_locked_overrides(
    parent: dict[PolicyKey, CapabilityParameterPolicy],
    child: dict[PolicyKey, CapabilityParameterPolicy],
    *,
    label: str,
) -> None:
    for key, child_config in child.items():
        parent_config = parent.get(key)
        if parent_config is None:
            continue
        for field in parent_config.locked:
            child_defaults = child_config.defaults
            parent_defaults = parent_config.defaults
            if field in child_defaults and child_defaults[field] != parent_defaults[field]:
                raise ValueError(f"{label} {key} overrides locked parameter {field}")
