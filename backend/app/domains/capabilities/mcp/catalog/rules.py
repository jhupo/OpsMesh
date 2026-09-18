from datetime import timedelta
from urllib.parse import urlparse
from uuid import UUID

from backend.app.core.security.egress import validate_url_shape
from backend.app.domains.capabilities.mcp.catalog.catalog import McpCatalogTool
from backend.app.domains.capabilities.mcp.models import McpCredentialReference, McpServer
from backend.app.domains.capabilities.mcp.policy import mcp_health_check_stale
from backend.app.domains.capabilities.resources.schema import reject_embedded_secrets

REMOTE_SERVER_TYPES = {"streamable_http", "sse"}
MCP_AUTH_METHODS = {
    "none",
    "static_header",
    "bearer_token",
    "credential_ref",
}


def normalize_connection(server_type: str, value: dict[str, object]) -> dict[str, object]:
    """Normalize transport/auth metadata without accepting secret-bearing headers."""

    connection = dict(value)
    reject_embedded_secrets(connection, path="connection")
    normalized_type = server_type.lower().strip()
    if normalized_type in REMOTE_SERVER_TYPES or normalized_type == "hosted":
        url = connection.get("url") or connection.get("endpoint")
        if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
            raise ValueError("Remote MCP server requires an http(s) url")
        validate_url_shape(url, allowed_schemes=frozenset({"http", "https"}))
        transport = str(
            connection.get("transport")
            or ("streamable_http" if normalized_type == "hosted" else normalized_type)
        ).lower().strip()
        if transport not in REMOTE_SERVER_TYPES:
            raise ValueError("Remote MCP server transport must be streamable_http or sse")
        auth_method = str(
            connection.get("auth_method")
            or (
                "credential_ref"
                if normalized_type == "hosted" or connection.get("requires_credentials") is True
                else "none"
            )
        ).lower().strip()
        if auth_method not in MCP_AUTH_METHODS:
            raise ValueError("MCP auth_method is unsupported")
        configured_requirement = connection.get("requires_credentials")
        if isinstance(configured_requirement, bool) and configured_requirement != (
            auth_method != "none"
        ):
            raise ValueError("MCP auth_method conflicts with requires_credentials")
        connection["transport"] = transport
        connection["auth_method"] = auth_method
        connection["requires_credentials"] = auth_method != "none"
    return connection


def credential_status(
    server: McpServer,
    *,
    credential_count: int,
    workspace_credential_count: int,
) -> str:
    if credential_count > 0:
        return "server_configured"
    if workspace_credential_count > 0:
        return "workspace_configured"
    if requires_credentials(server):
        return "missing_required"
    return "not_required"


def requires_credentials(server: McpServer) -> bool:
    configured = server.connection.get("requires_credentials")
    if isinstance(configured, bool):
        return configured
    return normalized_server_type(server) == "hosted"


def selected_remote_credentials(
    server: McpServer,
    credentials: list[McpCredentialReference],
) -> list[McpCredentialReference]:
    if not requires_credentials(server):
        return []
    eligible = [
        item
        for item in credentials
        if item.workspace_id == server.workspace_id
        and item.status == "active"
        and item.mcp_server_id in (None, server.id)
    ]
    bound_id = server.connection.get("credential_reference_id")
    if bound_id is not None:
        try:
            parsed_id = UUID(str(bound_id))
        except ValueError:
            return []
        return [item for item in eligible if item.id == parsed_id]
    return eligible if len(eligible) == 1 else []


def execution_mode(server: McpServer) -> str:
    server_type = normalized_server_type(server)
    if server_type == "stdio":
        runtime = server.connection.get("runtime")
        if runtime == "self_hosted":
            return "self_hosted_stdio"
        return "isolated_runtime_stdio"
    if server_type == "streamable_http":
        return "remote_http"
    if server_type == "sse":
        return "remote_sse"
    if server_type == "hosted":
        return "hosted"
    return "unsupported"


def mcp_blocked_reasons(
    server: McpServer,
    *,
    tools: list[McpCatalogTool],
    credential_status: str,
    stale_after: timedelta,
) -> list[str]:
    reasons = mcp_server_execution_blockers(
        server,
        credentials_ready=credential_status != "missing_required",
        stale_after=stale_after,
    )
    if not tools:
        reasons.append("no_allowed_tools")
    return reasons


def mcp_server_execution_blockers(
    server: McpServer,
    *,
    credentials_ready: bool,
    stale_after: timedelta,
) -> list[str]:
    reasons: list[str] = []
    server_type = normalized_server_type(server)
    if server.status != "active":
        reasons.append("server_inactive")
    if server.discovery_version > 0 and server.discovery_status != "succeeded":
        reasons.append("discovery_unready")
    if server.health_status != "healthy":
        reasons.append(
            "server_unhealthy" if server.health_status == "unhealthy" else "server_health_unready"
        )
    if server.last_health_check_at is None:
        reasons.append("health_check_missing")
    elif mcp_health_check_stale(server, stale_after=stale_after):
        reasons.append("health_check_stale")
    if not credentials_ready:
        reasons.append("missing_required_credentials")
    if execution_mode(server) == "unsupported":
        reasons.append("unsupported_server_type")
    if server_type == "stdio" and not has_stdio_command(server):
        reasons.append("missing_stdio_command")
    if server_type in REMOTE_SERVER_TYPES and not has_remote_url(server):
        reasons.append("missing_remote_url")
    if server_type == "hosted":
        transport = normalized_transport(server)
        if transport not in REMOTE_SERVER_TYPES:
            reasons.append("unsupported_hosted_transport")
        if not has_remote_url(server):
            reasons.append("missing_remote_url")
    return reasons


def connection_summary(server: McpServer) -> dict[str, object]:
    connection = server.connection
    summary: dict[str, object] = {
        "requires_credentials": requires_credentials(server),
    }
    transport = connection.get("transport")
    if isinstance(transport, str) and transport:
        summary["transport"] = transport
    auth_method = connection.get("auth_method")
    if isinstance(auth_method, str) and auth_method:
        summary["auth_method"] = auth_method
    url = connection.get("url") or connection.get("endpoint")
    if isinstance(url, str) and url:
        parsed = urlparse(url)
        summary["remote_host"] = parsed.netloc or None
        summary["has_remote_url"] = True
    else:
        summary["has_remote_url"] = False
    summary["has_stdio_command"] = has_stdio_command(server)
    return summary


def has_stdio_command(server: McpServer) -> bool:
    command = server.connection.get("command")
    if isinstance(command, str):
        return bool(command.strip())
    if isinstance(command, list):
        return any(isinstance(item, str) and bool(item.strip()) for item in command)
    return False


def has_remote_url(server: McpServer) -> bool:
    url = server.connection.get("url") or server.connection.get("endpoint")
    return isinstance(url, str) and url.lower().startswith(("https://", "http://"))


def mcp_server_probeable(server: McpServer) -> bool:
    server_type = normalized_server_type(server)
    if server_type == "stdio":
        return has_stdio_command(server)
    if server_type in REMOTE_SERVER_TYPES:
        return has_remote_url(server)
    if server_type == "hosted":
        return normalized_transport(server) in REMOTE_SERVER_TYPES and has_remote_url(server)
    return False


def normalized_server_type(server: McpServer) -> str:
    return server.server_type.lower().strip()


def normalized_transport(server: McpServer) -> str:
    return str(server.connection.get("transport") or "").lower().strip()
