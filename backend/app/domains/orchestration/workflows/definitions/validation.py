"""Persistence-backed validation for workspace workflow definitions."""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.capabilities.mcp.models import (
    McpServer,
    McpToolAllowlist,
)
from backend.app.domains.capabilities.resources.models import CapabilityResource
from backend.app.domains.orchestration.models import OrchestrationDefinition, OrchestrationRevision
from backend.app.domains.orchestration.workflows.definitions.contracts import WorkflowNode
from backend.app.domains.orchestration.workflows.definitions.graph import (
    ProjectPlanValidationError,
    WorkflowGraphError,
    validate_workflow_graph,
)


class DefinitionValidationError(ValueError):
    """A definition cannot be admitted under the workspace's current state."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class DefinitionValidationService:
    """Validate graph shape and workspace-owned execution references."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def validate_nodes(
        self,
        workspace_id: UUID,
        nodes: list[WorkflowNode],
        *,
        parent_definition_id: UUID | None = None,
    ) -> list[dict[str, object]]:
        self._validate_graph(nodes)
        self._validate_agent_profiles(workspace_id, nodes)
        self._validate_resources(workspace_id, nodes)
        self._validate_mcp(workspace_id, nodes)
        for node in nodes:
            if node.node_type == "subworkflow":
                self.require_subworkflow_reference(
                    workspace_id,
                    node,
                    parent_definition_id=parent_definition_id,
                )
        return [node.model_dump(mode="json", by_alias=True, exclude_none=True) for node in nodes]

    def require_subworkflow_reference(
        self,
        workspace_id: UUID,
        node: WorkflowNode,
        *,
        parent_definition_id: UUID | None = None,
    ) -> None:
        definition_id = node.subworkflow_definition_id
        if definition_id is None:
            raise DefinitionValidationError(
                "Subworkflow node requires a definition",
                code="orchestration_subworkflow_definition_invalid",
            )
        if parent_definition_id is not None and definition_id == parent_definition_id:
            raise DefinitionValidationError(
                "A subworkflow cannot reference its own definition",
                code="orchestration_recursive_subworkflow",
            )
        definition = self._session.scalar(
            select(OrchestrationDefinition).where(
                OrchestrationDefinition.workspace_id == workspace_id,
                OrchestrationDefinition.id == definition_id,
                OrchestrationDefinition.status != "archived",
            )
        )
        if definition is None:
            raise DefinitionValidationError(
                "Subworkflow definition is unavailable",
                code="orchestration_subworkflow_definition_invalid",
            )
        revision_query = select(OrchestrationRevision.id).where(
            OrchestrationRevision.workspace_id == workspace_id,
            OrchestrationRevision.definition_id == definition.id,
        )
        if node.subworkflow_version is not None:
            revision_query = revision_query.where(
                OrchestrationRevision.version == node.subworkflow_version
            )
        if self._session.scalar(revision_query.limit(1)) is None:
            raise DefinitionValidationError(
                "Subworkflow definition has no published revision",
                code="orchestration_subworkflow_revision_invalid",
            )

    def _validate_graph(self, nodes: list[WorkflowNode]) -> None:
        if not nodes or len(nodes) > 128:
            raise DefinitionValidationError(
                "Orchestration must contain 1 to 128 nodes",
                code="orchestration_node_count_invalid",
            )
        node_ids = [node.package_id for node in nodes]
        if len(set(node_ids)) != len(node_ids):
            raise DefinitionValidationError(
                "Orchestration node IDs must be unique",
                code="orchestration_duplicate_node",
            )
        if {"manager-planning", "manager-summary"}.intersection(node_ids):
            raise DefinitionValidationError(
                "Orchestration node ID is reserved",
                code="orchestration_reserved_node",
            )
        try:
            validate_workflow_graph(
                [node.model_dump(mode="json", by_alias=True, exclude_none=True) for node in nodes],
                set(node_ids),
            )
        except (ProjectPlanValidationError, WorkflowGraphError) as exc:
            raise DefinitionValidationError(str(exc), code=exc.code) from exc

    def _validate_agent_profiles(self, workspace_id: UUID, nodes: list[WorkflowNode]) -> None:
        profile_ids = {
            node.assigned_agent_profile_id
            for node in nodes
            if node.assigned_agent_profile_id is not None
        }
        if not profile_ids:
            return
        profiles = self._session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id.in_(profile_ids),
                AgentProfile.status == "active",
            )
        ).all()
        if {profile.id for profile in profiles} != profile_ids:
            raise DefinitionValidationError(
                "Orchestration references an unavailable agent profile",
                code="orchestration_agent_reference_invalid",
            )

    def _validate_resources(self, workspace_id: UUID, nodes: list[WorkflowNode]) -> None:
        resource_ids = {resource_id for node in nodes for resource_id in node.required_resource_ids}
        if not resource_ids:
            return
        resources = self._session.scalars(
            select(CapabilityResource).where(
                CapabilityResource.workspace_id == workspace_id,
                CapabilityResource.id.in_(resource_ids),
                CapabilityResource.status == "active",
            )
        ).all()
        if {resource.id for resource in resources} != resource_ids:
            raise DefinitionValidationError(
                "Orchestration references an unavailable resource",
                code="orchestration_resource_reference_invalid",
            )

    def _validate_mcp(self, workspace_id: UUID, nodes: list[WorkflowNode]) -> None:
        server_ids = {item.mcp_server_id for node in nodes for item in node.required_mcp_tools}
        servers = self._session.scalars(
            select(McpServer).where(
                McpServer.workspace_id == workspace_id,
                McpServer.id.in_(server_ids),
                McpServer.status == "active",
            )
        ).all() if server_ids else []
        if {server.id for server in servers} != server_ids:
            raise DefinitionValidationError(
                "Orchestration references an unavailable MCP server",
                code="orchestration_mcp_server_reference_invalid",
            )
        for node in nodes:
            for item in node.required_mcp_tools:
                allowlist = self._session.scalar(
                    select(McpToolAllowlist).where(
                        McpToolAllowlist.workspace_id == workspace_id,
                        McpToolAllowlist.mcp_server_id == item.mcp_server_id,
                        McpToolAllowlist.tool_name == item.tool_name,
                        McpToolAllowlist.status == "active",
                    )
                )
                if allowlist is None or (
                    item.mcp_tool_allowlist_id is not None
                    and allowlist.id != item.mcp_tool_allowlist_id
                ):
                    raise DefinitionValidationError(
                        f"MCP tool {item.tool_name} is not allowlisted",
                        code="orchestration_mcp_tool_reference_invalid",
                    )


def nodes_from_definition(
    definition: OrchestrationDefinition | OrchestrationRevision,
) -> list[WorkflowNode]:
    raw_definition = definition.definition
    raw_nodes = raw_definition.get("nodes") if isinstance(raw_definition, dict) else None
    if not isinstance(raw_nodes, list):
        raise DefinitionValidationError(
            "Stored orchestration has no nodes",
            code="orchestration_definition_invalid",
        )
    try:
        return [WorkflowNode.model_validate(item) for item in raw_nodes]
    except ValidationError as exc:
        raise DefinitionValidationError(
            "Stored orchestration node is invalid",
            code="orchestration_definition_invalid",
        ) from exc
