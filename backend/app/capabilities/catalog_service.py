from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.schemas.capabilities.catalog import (
    CapabilityResourceResponse,
    CapabilityToolDescriptor,
    WorkspaceCapabilityCatalogResponse,
)
from backend.app.capabilities.mcp_servers import McpServerService
from backend.app.capabilities.product_tool_catalog import PRODUCT_TOOL_CATALOG
from backend.app.capabilities.resource_service import CapabilityResourceService


class WorkspaceCapabilityCatalogService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def build(self, workspace_id: UUID) -> WorkspaceCapabilityCatalogResponse:
        resources = CapabilityResourceService(self._session).list_active_resources(workspace_id)
        mcp_tools = McpServerService(self._session).list_allowed_mcp_tools(workspace_id)
        tools = [
            CapabilityToolDescriptor(
                name=definition.name,
                source="product",
                description=definition.description,
                input_schema=definition.input_schema,
                requires_approval=definition.requires_approval,
                risk_level=definition.risk_level,
                required_resource_type=definition.required_resource_type,
                required_access_modes=list(definition.required_access_modes),
            )
            for definition in PRODUCT_TOOL_CATALOG
        ]
        tools.extend(
            CapabilityToolDescriptor(
                name=allow.tool_name,
                source="mcp",
                description=allow.description,
                input_schema=allow.input_schema,
                requires_approval=allow.requires_approval,
                risk_level=allow.risk_level,
                capability_key=allow.capability_key,
                mcp_server_id=server.id,
                mcp_tool_allowlist_id=allow.id,
                mcp_server_name=server.name,
                policy=allow.policy,
            )
            for allow, server in mcp_tools
        )
        return WorkspaceCapabilityCatalogResponse(
            workspace_id=workspace_id,
            tools=sorted(tools, key=lambda item: (item.name, item.source)),
            resources=[CapabilityResourceResponse.model_validate(item) for item in resources],
        )
