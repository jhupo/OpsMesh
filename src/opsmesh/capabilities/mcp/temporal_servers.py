"""Worker-side MCP factories for the official Temporal Agents integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any
from uuid import UUID

from agents.mcp import (
    MCPServer,
    MCPServerSse,
    MCPServerSseParams,
    MCPServerStreamableHttp,
    MCPServerStreamableHttpParams,
)
from agents.mcp.util import create_static_tool_filter
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.authorized import AuthorizedMCPServer
from opsmesh.capabilities.mcp.catalog.rules import normalized_server_type, normalized_transport
from opsmesh.capabilities.mcp.execution.contracts import MCPExecutionRequest
from opsmesh.capabilities.mcp.execution.notifications import MCPNotificationRecorder
from opsmesh.capabilities.mcp.execution.validation import MCPExecutionValidator
from opsmesh.capabilities.mcp.models import MCPDeployment
from opsmesh.capabilities.mcp.sandbox_stdio import SandboxMCPServerStdio
from opsmesh.capabilities.mcp.transport.credentials import resolve_hosted_credentials
from opsmesh.capabilities.mcp.transport.payloads import stdio_command
from opsmesh.capabilities.mcp.transport.remote import validate_mcp_auth_headers, validate_mcp_url
from opsmesh.capabilities.tools.contracts import ToolPermissionError
from opsmesh.runtime.sandboxes.registry import SandboxBackendRegistry
from opsmesh.shared.config import Settings
from opsmesh.shared.errors import PolicyDeniedError
from opsmesh.shared.security.egress import MCP_EGRESS_URL_POLICY
from opsmesh.shared.security.secrets import SecretEncryptionService

SessionFactory = Callable[[], AbstractContextManager[Session]]
MCPServerFactory = Callable[[Any | None], MCPServer]


def build_temporal_mcp_servers(
    session_factory: SessionFactory,
    settings: Settings,
    backends: SandboxBackendRegistry,
) -> Mapping[str, MCPServerFactory]:
    """Build the named worker factory consumed by ``OpenAIAgentsPlugin``.

    The workflow passes only workspace/server identifiers as ``factory_argument``. The
    worker resolves authorization state and decrypts credentials immediately before
    creating the SDK MCP client; no credential or connection secret enters workflow
    history.
    """

    def factory(argument: Any | None = None) -> MCPServer:
        if not isinstance(argument, Mapping):
            raise ValueError("MCP factory requires an authorized Agent reference")
        try:
            workspace_id = UUID(str(argument["workspace_id"]))
            run_id = UUID(str(argument["run_id"]))
            server_id = UUID(str(argument["server_id"]))
            agent_profile_id = UUID(str(argument["agent_profile_id"]))
            fingerprint = argument["capability_catalog_fingerprint"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("MCP Agent authorization reference is invalid") from exc
        if not isinstance(fingerprint, str):
            raise ValueError("MCP Agent capability fingerprint is invalid")
        reference = MCPExecutionRequest(
            workspace_id=workspace_id,
            agent_run_id=run_id,
            agent_profile_id=agent_profile_id,
            capability_catalog_fingerprint=fingerprint,
            mcp_server_id=server_id,
            tool_name="",
            arguments={},
        )
        return AuthorizedMCPServer(
            lambda: _create_server(session_factory, settings, backends, reference),
            sessions=session_factory,
            settings=settings,
            workspace_id=workspace_id,
            run_id=run_id,
            server_id=server_id,
            agent_profile_id=agent_profile_id,
            capability_catalog_fingerprint=fingerprint,
        )

    return {"opsmesh": factory}


def _create_server(
    session_factory: SessionFactory,
    settings: Settings,
    backends: SandboxBackendRegistry,
    reference: MCPExecutionRequest,
) -> MCPServer:
    workspace_id = reference.workspace_id
    with session_factory() as session:
        try:
            authorized = MCPExecutionValidator(session, settings).authorize_connection(reference)
        except ToolPermissionError:
            # Admission denial evidence is a committed product outcome even when
            # the surrounding Activity fails before a protocol client exists.
            session.commit()
            raise
        if authorized.run.status != "running":
            raise PolicyDeniedError("MCP connection belongs to an inactive run")
        server = authorized.server
        notifications = MCPNotificationRecorder(
            session_factory,
            workspace_id=workspace_id,
            run_id=authorized.run.id,
            server_id=server.id,
        )
        server_type = normalized_server_type(server)
        secrets = SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
            previous_secrets=settings.credential_encryption_previous_secrets,
        )
        connection = dict(server.connection)
        env, headers = resolve_hosted_credentials(list(authorized.credentials), secrets)
        if server_type == "stdio":
            command = stdio_command(connection)
            if connection.get("runtime") == "managed":
                deployment = session.scalar(
                    select(MCPDeployment).where(
                        MCPDeployment.workspace_id == workspace_id,
                        MCPDeployment.mcp_server_id == server.id,
                        MCPDeployment.status == "ready",
                    )
                )
                if deployment is None:
                    raise PolicyDeniedError("Managed MCP deployment is not ready")
                credential_id = connection.get("credential_reference_id")
                credential = next(
                    (item for item in authorized.credentials if str(item.id) == credential_id),
                    None,
                )
                if (
                    deployment.server_version != server.configuration_version
                    or deployment.credential_version
                    != (credential.configuration_version if credential is not None else None)
                ):
                    raise PolicyDeniedError(
                        "Managed MCP readiness is stale; refresh the deployment"
                    )
                runtime_id = deployment.runtime_id
            else:
                if authorized.run.runtime_id is None:
                    raise PolicyDeniedError("MCP run has no authorized sandbox environment")
                runtime_id = authorized.run.runtime_id
            cwd = connection.get("cwd")
            if cwd is not None and not isinstance(cwd, str):
                raise PolicyDeniedError("MCP working directory is invalid")
            return SandboxMCPServerStdio(
                session_factory=session_factory,
                backends=backends,
                secrets=secrets,
                workspace_id=workspace_id,
                runtime_id=runtime_id,
                server_id=server.id,
                command=command,
                environment=env,
                cwd=cwd,
                name=server.name,
                timeout_seconds=settings.mcp_tool_timeout_seconds,
                tool_names=authorized.tool_names,
                message_handler=notifications.notify,
            )
        url = connection.get("url")
        if not isinstance(url, str) or not url.strip():
            raise PolicyDeniedError("MCP server URL is missing")
        transport = normalized_transport(server) if server_type == "hosted" else server_type
        validate_mcp_url(url, egress_policy=MCP_EGRESS_URL_POLICY, transport=transport)
        validate_mcp_auth_headers(server, headers)
        http_params: MCPServerStreamableHttpParams = {"url": url}
        if headers:
            http_params["headers"] = headers
        if transport == "streamable_http":
            return MCPServerStreamableHttp(
                params=http_params,
                name=server.name,
                client_session_timeout_seconds=settings.mcp_tool_timeout_seconds,
                max_retry_attempts=0,
                require_approval="never",
                failure_error_function=None,
                message_handler=notifications.notify,
                tool_filter=create_static_tool_filter(
                    allowed_tool_names=list(authorized.tool_names)
                ),
            )
        if transport == "sse":
            sse_params: MCPServerSseParams = {"url": url}
            if headers:
                sse_params["headers"] = headers
            return MCPServerSse(
                params=sse_params,
                name=server.name,
                client_session_timeout_seconds=settings.mcp_tool_timeout_seconds,
                max_retry_attempts=0,
                require_approval="never",
                failure_error_function=None,
                message_handler=notifications.notify,
                tool_filter=create_static_tool_filter(
                    allowed_tool_names=list(authorized.tool_names)
                ),
            )
        raise PolicyDeniedError("MCP server transport is not supported")
