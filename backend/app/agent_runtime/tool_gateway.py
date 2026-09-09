from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import (
    AgentRuntimeContext,
    AgentRuntimeResourceGrant,
    AgentRuntimeToolDefinition,
)
from backend.app.capabilities.models import CapabilityResource
from backend.app.capabilities.schema_validation import validate_parameters
from backend.app.core.trace_context import with_current_trace_metadata
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.runs.models import AgentRun
from backend.app.security.models import SecurityEvent


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    definition: AgentRuntimeToolDefinition
    arguments: dict[str, object]
    resource_grants: tuple[AgentRuntimeResourceGrant, ...]


class ToolGatewayDenied(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AgentToolGateway:
    def __init__(self, session: Session) -> None:
        self._session = session

    def prepare(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> PreparedToolCall:
        matches = [
            definition
            for definition in context.tool_definitions
            if definition.name == tool_name
        ]
        if tool_name not in context.allowed_tools or not matches:
            raise ToolGatewayDenied("tool_not_in_run_manifest", "Tool is not in the run manifest")
        if len(matches) != 1:
            raise ToolGatewayDenied("tool_manifest_ambiguous", "Tool manifest entry is ambiguous")
        definition = matches[0]
        merged = dict(definition.parameters)
        for field, value in arguments.items():
            if field in definition.locked_parameters and merged.get(field) != value:
                raise ToolGatewayDenied(
                    "tool_parameter_locked",
                    f"Tool parameter {field} is locked by policy",
                )
            merged[field] = value
        try:
            validate_parameters(merged, definition.input_schema, label="tool arguments")
        except ValueError as exc:
            raise ToolGatewayDenied("tool_arguments_invalid", str(exc)) from exc
        grants = self._relevant_resource_grants(context, definition)
        self._require_active_resources(context.workspace_id, grants)
        self._require_product_resource_scope(context, definition, grants, merged)
        return PreparedToolCall(
            definition=definition,
            arguments=merged,
            resource_grants=grants,
        )

    def record_denial(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        denial: ToolGatewayDenied,
    ) -> None:
        run = self._session.get(AgentRun, context.run_id)
        if run is not None and run.workspace_id == context.workspace_id:
            RunEventRecorder(self._session).append_event(
                run,
                "tool.blocked",
                tool_name,
                {
                    "tool_name": tool_name,
                    "reason": denial.code,
                    "capability_catalog_fingerprint": context.metadata.get(
                        "capability_catalog_fingerprint"
                    ),
                },
            )
        self._session.add(
            SecurityEvent(
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                action="agent_tool.blocked",
                outcome="blocked",
                severity="high",
                source_ip=None,
                user_agent=None,
                request_id=None,
                path="internal:agent_tool_gateway",
                method="WORKER",
                reason=denial.code,
                event_metadata=with_current_trace_metadata(
                    {
                        "agent_run_id": str(context.run_id),
                        "tool_name": tool_name,
                        "message": str(denial),
                        "authorization_snapshot_fingerprint": context.metadata.get(
                            "authorization_snapshot_fingerprint"
                        ),
                        "capability_catalog_fingerprint": context.metadata.get(
                            "capability_catalog_fingerprint"
                        ),
                    }
                ),
                created_at=datetime.now(UTC),
            )
        )
        self._session.commit()

    def _relevant_resource_grants(
        self,
        context: AgentRuntimeContext,
        definition: AgentRuntimeToolDefinition,
    ) -> tuple[AgentRuntimeResourceGrant, ...]:
        required_type = definition.required_resource_type
        if required_type is not None:
            return tuple(
                grant
                for grant in context.resource_grants
                if grant.resource_type == required_type
                and grant.access_mode in definition.required_access_modes
            )
        if definition.source == "mcp" and definition.mcp_server_id is not None:
            server_id = str(definition.mcp_server_id)
            return tuple(
                grant
                for grant in context.resource_grants
                if grant.resource_type in {"mcp_resource", "external_service"}
                and grant.locator.get("mcp_server_id") == server_id
            )
        return ()

    def _require_active_resources(
        self,
        workspace_id: UUID,
        grants: tuple[AgentRuntimeResourceGrant, ...],
    ) -> None:
        if not grants:
            return
        active_ids = set(
            self._session.scalars(
                select(CapabilityResource.id).where(
                    CapabilityResource.workspace_id == workspace_id,
                    CapabilityResource.status == "active",
                    CapabilityResource.id.in_([grant.resource_id for grant in grants]),
                )
            ).all()
        )
        if any(grant.resource_id not in active_ids for grant in grants):
            raise ToolGatewayDenied(
                "capability_resource_disabled",
                "A required capability resource is disabled",
            )

    def _require_product_resource_scope(
        self,
        context: AgentRuntimeContext,
        definition: AgentRuntimeToolDefinition,
        grants: tuple[AgentRuntimeResourceGrant, ...],
        arguments: dict[str, object],
    ) -> None:
        required_type = definition.required_resource_type
        if required_type is None:
            return
        if not grants:
            raise ToolGatewayDenied(
                "capability_resource_required",
                f"Tool {definition.name} requires an authorized {required_type} resource",
            )
        if definition.name == "read_workspace_file":
            file_id = str(arguments.get("file_id") or "")
            if file_id not in _allowed_file_ids(context, grants):
                raise ToolGatewayDenied(
                    "workspace_file_not_in_resource_scope",
                    "Workspace file is outside the run resource scope",
                )
        if definition.name == "search_workspace_memory":
            allowed_source_types = _memory_locator_values(grants, "source_types")
            requested_source_types = arguments.get("source_types")
            if (
                isinstance(requested_source_types, list)
                and allowed_source_types
                and any(item not in allowed_source_types for item in requested_source_types)
            ):
                raise ToolGatewayDenied(
                    "memory_source_type_not_in_resource_scope",
                    "Memory source type is outside the run resource scope",
                )
        if definition.name == "upsert_semantic_memory":
            self._require_memory_scope(arguments, grants)
            allowed_tags = _memory_locator_values(grants, "tags")
            requested_tags = arguments.get("tags")
            if allowed_tags and (
                not isinstance(requested_tags, list)
                or not allowed_tags.intersection(
                    item for item in requested_tags if isinstance(item, str)
                )
            ):
                raise ToolGatewayDenied(
                    "memory_tags_not_in_resource_scope",
                    "Memory tags are outside the run resource scope",
                )
        if definition.name == "archive_semantic_memory":
            entry_id = arguments.get("memory_entry_id")
            try:
                memory_entry_id = UUID(str(entry_id))
            except (TypeError, ValueError) as exc:
                raise ToolGatewayDenied(
                    "semantic_memory_id_invalid",
                    "Semantic memory ID is invalid",
                ) from exc
            entry = self._session.get(WorkspaceMemoryEntry, memory_entry_id)
            if (
                entry is None
                or entry.workspace_id != context.workspace_id
                or entry.memory_layer != "semantic"
                or entry.source_type != "semantic_memory"
            ):
                raise ToolGatewayDenied(
                    "semantic_memory_not_found",
                    "Semantic memory is unavailable in the current workspace",
                )
            self._require_memory_scope(
                {"scope_type": entry.scope_type, "scope_id": entry.scope_id},
                grants,
            )

    @staticmethod
    def _require_memory_scope(
        arguments: dict[str, object],
        grants: tuple[AgentRuntimeResourceGrant, ...],
    ) -> None:
        scope_type = arguments.get("scope_type")
        scope_id = arguments.get("scope_id")
        allowed_scope_types = _memory_locator_values(grants, "scope_types")
        allowed_scope_ids = _memory_locator_values(grants, "scope_ids")
        if (
            not isinstance(scope_type, str)
            or allowed_scope_types
            and scope_type not in allowed_scope_types
        ):
            raise ToolGatewayDenied(
                "memory_scope_type_not_in_resource_scope",
                "Memory scope type is outside the run resource scope",
            )
        if allowed_scope_ids and str(scope_id) not in allowed_scope_ids:
            raise ToolGatewayDenied(
                "memory_scope_id_not_in_resource_scope",
                "Memory scope ID is outside the run resource scope",
            )


def _allowed_file_ids(
    context: AgentRuntimeContext,
    grants: tuple[AgentRuntimeResourceGrant, ...],
) -> set[str]:
    allowed = {
        str(file_id)
        for grant in grants
        for file_id in _locator_strings(grant, "file_ids")
    }
    if context.file_scope_ids:
        allowed &= {str(file_id) for file_id in context.file_scope_ids}
    return allowed


def _memory_locator_values(
    grants: tuple[AgentRuntimeResourceGrant, ...],
    key: str,
) -> set[str]:
    return {
        item
        for grant in grants
        for item in _locator_strings(grant, key)
    }


def _locator_strings(grant: AgentRuntimeResourceGrant, key: str) -> tuple[str, ...]:
    value = grant.locator.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
