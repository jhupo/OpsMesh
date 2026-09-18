from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.domains.capabilities.catalog.contracts import (
    CapabilityMcpCredentialBinding,
    CapabilityResourceResponse,
    CapabilityToolDescriptor,
    WorkspaceCapabilityCatalogResponse,
)
from backend.app.domains.capabilities.catalog.product_tools import PRODUCT_TOOL_CATALOG
from backend.app.domains.capabilities.mcp.catalog.rules import (
    mcp_server_execution_blockers,
    requires_credentials,
    selected_remote_credentials,
)
from backend.app.domains.capabilities.mcp.catalog.servers import McpServerService
from backend.app.domains.capabilities.mcp.models import McpCredentialReference
from backend.app.domains.capabilities.resources.service import CapabilityResourceService


class WorkspaceCapabilityCatalogService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def build(self, workspace_id: UUID) -> WorkspaceCapabilityCatalogResponse:
        resources = CapabilityResourceService(self._session).list_active_resources(workspace_id)
        mcp_tools = McpServerService(self._session).list_allowed_mcp_tools(workspace_id)
        credentials = list(
            self._session.scalars(
                select(McpCredentialReference)
                .where(
                    McpCredentialReference.workspace_id == workspace_id,
                    McpCredentialReference.status == "active",
                )
                .order_by(
                    McpCredentialReference.created_at.asc(),
                    McpCredentialReference.id.asc(),
                )
            )
        )
        tools = [
            CapabilityToolDescriptor(
                name=definition.name,
                title="",
                source="product",
                description=definition.description,
                input_schema=definition.input_schema,
                output_schema={},
                requires_approval=definition.requires_approval,
                risk_level=definition.risk_level,
                required_resource_type=definition.required_resource_type,
                required_access_modes=list(definition.required_access_modes),
            )
            for definition in PRODUCT_TOOL_CATALOG
        ]
        stale_after = timedelta(seconds=self._settings.mcp_health_check_stale_after_seconds)
        for allow, server in mcp_tools:
            eligible_credentials = [
                credential
                for credential in credentials
                if credential.mcp_server_id in (None, server.id)
            ]
            credentials_required = requires_credentials(server)
            if server.server_type in {"streamable_http", "sse", "hosted"}:
                eligible_credentials = selected_remote_credentials(server, eligible_credentials)
            tools.append(
                CapabilityToolDescriptor(
                    name=allow.tool_name,
                    title=allow.title,
                    source="mcp",
                    description=allow.description,
                    input_schema=allow.input_schema,
                    output_schema=allow.output_schema,
                    requires_approval=allow.requires_approval,
                    risk_level=allow.risk_level,
                    capability_key=allow.capability_key,
                    mcp_server_id=server.id,
                    mcp_tool_allowlist_id=allow.id,
                    mcp_server_name=server.name,
                    mcp_server_type=server.server_type,
                    mcp_server_configuration_version=server.configuration_version,
                    mcp_tool_configuration_version=allow.configuration_version,
                    mcp_requires_credentials=credentials_required,
                    mcp_credential_references=[
                        CapabilityMcpCredentialBinding(
                            credential_reference_id=credential.id,
                            mcp_server_id=credential.mcp_server_id,
                            configuration_version=credential.configuration_version,
                            provider=credential.provider,
                            scopes=list(credential.scopes),
                            secret_fingerprint=credential.secret_fingerprint,
                            encryption_key_id=credential.encryption_key_id,
                        )
                        for credential in eligible_credentials
                    ],
                    mcp_blocked_reasons=mcp_server_execution_blockers(
                        server,
                        credentials_ready=(not credentials_required or bool(eligible_credentials)),
                        stale_after=stale_after,
                    ),
                    policy=allow.policy,
                )
            )
        return WorkspaceCapabilityCatalogResponse(
            workspace_id=workspace_id,
            tools=sorted(tools, key=lambda item: (item.name, item.source)),
            resources=[CapabilityResourceResponse.model_validate(item) for item in resources],
        )
