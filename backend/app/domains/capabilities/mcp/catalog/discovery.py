from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.core.security.egress import MCP_EGRESS_URL_POLICY
from backend.app.core.security.redaction import redact_sensitive_payload, redact_sensitive_text
from backend.app.core.security.secrets import SecretEncryptionService
from backend.app.domains.capabilities.mcp.catalog.rules import (
    normalized_server_type,
    requires_credentials,
    selected_remote_credentials,
)
from backend.app.domains.capabilities.mcp.models import (
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
)
from backend.app.domains.capabilities.mcp.transport.remote import (
    credential_headers,
    list_remote_mcp_tools_async,
    validate_mcp_auth_headers,
    validate_mcp_url,
)
from backend.app.domains.capabilities.plugins.policy import require_plugin_resource
from backend.app.domains.capabilities.resources.schema import (
    normalize_object_schema,
    reject_embedded_secrets,
    validate_json_schema,
)
from backend.app.observability.audit.service import AuditService


class McpDiscoveryError(RuntimeError):
    pass


class McpDiscoveryConflict(McpDiscoveryError):
    pass


class McpToolDiscoveryService:
    def __init__(
        self,
        session: Session,
        *,
        secret_service: SecretEncryptionService | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self._session = session
        self._secret_service = secret_service
        self._timeout_seconds = timeout_seconds

    async def discover(
        self,
        *,
        workspace_id: UUID,
        server_id: UUID,
        credential_id: UUID | None = None,
        actor_user_id: UUID | None = None,
    ) -> dict[str, object]:
        server = self._session.scalar(
            select(McpServer).where(
                McpServer.workspace_id == workspace_id,
                McpServer.id == server_id,
                McpServer.status == "active",
            )
        )
        if server is None:
            raise McpDiscoveryError("MCP server not found or inactive")
        require_plugin_resource(self._session, workspace_id, "mcp_server", server_id)
        configuration_version = server.configuration_version
        try:
            if normalized_server_type(server) not in {"streamable_http", "sse", "hosted"}:
                raise McpDiscoveryError("Automatic discovery requires a remote MCP server")
            url = self._url(server)
            transport = self._transport(server)
            validate_mcp_url(url, egress_policy=MCP_EGRESS_URL_POLICY, transport=transport)
            credentials = self._credentials(workspace_id, server, credential_id)
            headers = credential_headers(credentials, secret_service=self._secret_service)
            validate_mcp_auth_headers(server, headers)
            tools = await list_remote_mcp_tools_async(
                url=url,
                headers=headers,
                timeout_seconds=self._timeout_seconds,
                transport="http" if transport == "streamable_http" else "sse",
            )
            return self._persist(
                workspace_id,
                server,
                tools,
                credential_id=credentials[0].id if credentials else None,
                credential_version=credentials[0].configuration_version if credentials else None,
                actor_user_id=actor_user_id,
                configuration_version=configuration_version,
            )
        except McpDiscoveryConflict:
            self._session.rollback()
            raise
        except McpDiscoveryError as exc:
            self._session.rollback()
            self._mark_failed(server, str(exc), actor_user_id=actor_user_id)
            raise
        except Exception as exc:
            self._session.rollback()
            self._mark_failed(server, exc.__class__.__name__, actor_user_id=actor_user_id)
            raise McpDiscoveryError("MCP tool discovery failed") from exc

    def _mark_failed(
        self,
        server: McpServer,
        error: str,
        *,
        actor_user_id: UUID | None,
    ) -> None:
        server.discovery_status = "failed"
        server.discovery_error = redact_sensitive_text(error)[:2_000]
        server.health_status = "unhealthy"
        server.last_health_check_at = datetime.now(UTC)
        server.last_error = "mcp_discovery_failed"
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=server.workspace_id,
                user_id=actor_user_id,
                action="mcp_server.discovery_failed",
                target_type="mcp_server",
                target_id=server.id,
                metadata={"error": server.discovery_error},
            )
        self._session.commit()

    def _persist(
        self,
        workspace_id: UUID,
        server: McpServer,
        tools: list[dict[str, object]],
        *,
        credential_id: UUID | None,
        credential_version: int | None,
        actor_user_id: UUID | None,
        configuration_version: int,
    ) -> dict[str, object]:
        self._session.refresh(server, with_for_update=True)
        if server.status != "active" or server.configuration_version != configuration_version:
            raise McpDiscoveryConflict(
                "MCP server changed during discovery; retry with current configuration"
            )
        if credential_id is not None:
            credential = self._session.scalar(
                select(McpCredentialReference)
                .where(
                    McpCredentialReference.id == credential_id,
                    McpCredentialReference.workspace_id == workspace_id,
                )
                .with_for_update()
            )
            if (
                credential is None
                or credential.status != "active"
                or credential.configuration_version != credential_version
            ):
                raise McpDiscoveryConflict(
                    "MCP credential changed during discovery; retry with current configuration"
                )
        now = datetime.now(UTC)
        normalized_items = [self._normalize_tool(item) for item in tools]
        names = [str(item["tool_name"]) for item in normalized_items]
        if len(set(names)) != len(names):
            raise McpDiscoveryError("MCP discovery returned duplicate tool names")
        normalized = {str(item["tool_name"]): item for item in normalized_items}
        checksum = _checksum(list(normalized.values()))
        rows = list(
            self._session.scalars(
                select(McpToolAllowlist).where(
                    McpToolAllowlist.workspace_id == workspace_id,
                    McpToolAllowlist.mcp_server_id == server.id,
                )
            )
        )
        by_name = {row.tool_name: row for row in rows}
        credential_changed = credential_id is not None and server.connection.get(
            "credential_reference_id"
        ) != str(credential_id)
        added: list[str] = []
        changed: list[str] = []
        for name, item in normalized.items():
            row = by_name.get(name)
            tool_checksum = _checksum([item])
            if row is None:
                row = McpToolAllowlist(
                    workspace_id=workspace_id,
                    mcp_server_id=server.id,
                    status="discovered",
                    discovery_source="mcp",
                    discovery_status="current",
                    discovery_checksum=tool_checksum,
                    discovered_at=now,
                    **item,
                )
                self._session.add(row)
                added.append(name)
                continue
            needs_review = (
                credential_changed
                or row.discovery_source != "mcp"
                or row.discovery_checksum != tool_checksum
                or row.status == "removed"
                or row.discovery_status == "stale"
            )
            if needs_review:
                changed.append(name)
                row.status = "discovered"
                row.discovery_status = "changed"
                row.configuration_version += 1
                row.requires_approval = True
                row.risk_level = "medium"
                input_schema = item["input_schema"]
                output_schema = item["output_schema"]
                policy = item["policy"]
                if (
                    not isinstance(input_schema, dict)
                    or not isinstance(output_schema, dict)
                    or not isinstance(policy, dict)
                ):
                    raise McpDiscoveryError("MCP tool metadata is invalid")
                row.title = str(item["title"])
                row.description = str(item["description"])
                row.input_schema = dict(input_schema)
                row.output_schema = dict(output_schema)
                row.policy = {
                    **row.policy,
                    "discovered_annotations": policy["discovered_annotations"],
                }
            else:
                row.discovery_status = "current"
            row.discovery_source = "mcp"
            row.discovery_checksum = tool_checksum
            row.discovered_at = now
        removed: list[str] = []
        for row in rows:
            if row.discovery_source != "mcp":
                continue
            if row.tool_name in normalized or row.status == "removed":
                continue
            row.status = "removed"
            row.discovery_status = "removed"
            row.discovery_source = "mcp"
            row.configuration_version += 1
            removed.append(row.tool_name)
        server.discovery_status = "succeeded"
        server.discovery_version += 1
        server.discovered_at = now
        server.discovery_checksum = checksum
        server.discovery_error = None
        server.health_status = "healthy"
        server.last_health_check_at = now
        server.last_error = None
        if credential_changed:
            server.connection = {**server.connection, "credential_reference_id": str(credential_id)}
            server.configuration_version += 1
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_server.tools_discovered",
                target_type="mcp_server",
                target_id=server.id,
                metadata={
                    "discovery_version": server.discovery_version,
                    "discovery_checksum": checksum,
                    "tool_count": len(normalized),
                    "added_tools": added,
                    "changed_tools": changed,
                    "removed_tools": removed,
                },
            )
        self._session.commit()
        return {
            "workspace_id": workspace_id,
            "mcp_server_id": server.id,
            "status": "succeeded",
            "discovery_version": server.discovery_version,
            "discovered_at": server.discovered_at,
            "discovery_checksum": checksum,
            "tool_count": len(normalized),
            "added_tools": sorted(added),
            "changed_tools": sorted(changed),
            "removed_tools": sorted(removed),
            "error": None,
        }

    def _credentials(
        self,
        workspace_id: UUID,
        server: McpServer,
        credential_id: UUID | None,
    ) -> list[McpCredentialReference]:
        if not requires_credentials(server):
            if credential_id is not None:
                raise McpDiscoveryError("MCP server does not require credentials")
            return []
        query = select(McpCredentialReference).where(
            McpCredentialReference.workspace_id == workspace_id,
            McpCredentialReference.status == "active",
            or_(
                McpCredentialReference.mcp_server_id.is_(None),
                McpCredentialReference.mcp_server_id == server.id,
            ),
        )
        credentials = list(self._session.scalars(query))
        if credential_id is None:
            selected = selected_remote_credentials(server, credentials)
            if len(selected) != 1:
                raise McpDiscoveryError("Select one active MCP credential before discovery")
            return selected
        selected = [item for item in credentials if item.id == credential_id]
        if not selected:
            raise McpDiscoveryError("Credential is not active or not bound to this MCP server")
        return selected

    @staticmethod
    def _url(server: McpServer) -> str:
        value = server.connection.get("url") or server.connection.get("endpoint")
        if not isinstance(value, str) or not value:
            raise McpDiscoveryError("MCP server is missing a remote URL")
        return value

    @staticmethod
    def _transport(server: McpServer) -> str:
        value = str(server.connection.get("transport") or server.server_type).lower().strip()
        if value == "hosted":
            value = str(server.connection.get("transport") or "streamable_http")
        if value not in {"streamable_http", "sse"}:
            raise McpDiscoveryError("MCP server transport is not discoverable")
        return value

    @staticmethod
    def _normalize_tool(item: dict[str, object]) -> dict[str, object]:
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise McpDiscoveryError("MCP discovery returned a tool without a name")
        reject_embedded_secrets(name, path="tool_name")
        input_schema = item.get("inputSchema") or item.get("input_schema") or {}
        if not isinstance(input_schema, dict):
            raise McpDiscoveryError(f"MCP tool {name.strip()} input schema is invalid")
        output_schema = item.get("outputSchema") or item.get("output_schema") or {}
        if not isinstance(output_schema, dict):
            raise McpDiscoveryError(f"MCP tool {name.strip()} output schema is invalid")
        normalized_output_schema = dict(output_schema)
        validate_json_schema(normalized_output_schema)
        reject_embedded_secrets(input_schema, path="input_schema")
        reject_embedded_secrets(normalized_output_schema, path="output_schema")
        annotations = item.get("annotations")
        title = redact_sensitive_text(str(item.get("title") or ""))
        description = redact_sensitive_text(str(item.get("description") or ""))
        if len(name.strip()) > 160 or len(title) > 240 or len(description) > 2_000:
            raise McpDiscoveryError("MCP discovery returned oversized tool metadata")
        if len(_canonical_json(item).encode("utf-8")) > 64_000:
            raise McpDiscoveryError("MCP discovery returned an oversized tool schema")
        return {
            "tool_name": name.strip(),
            "title": title,
            "description": description,
            "input_schema": normalize_object_schema(input_schema),
            "output_schema": normalized_output_schema,
            "capability_key": None,
            "requires_approval": True,
            "risk_level": "medium",
            "policy": {
                "discovered_annotations": redact_sensitive_payload(annotations)
                if isinstance(annotations, dict)
                else {}
            },
        }


def _checksum(value: object) -> str:
    encoded = _canonical_json(value)
    return f"sha256:{sha256(encoded.encode()).hexdigest()}"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
