from datetime import timedelta
from urllib.parse import urlparse

from backend.app.capabilities.mcp_catalog import McpCatalogTool
from backend.app.capabilities.mcp_policy import mcp_health_check_stale
from backend.app.capabilities.models import McpServer

REMOTE_SERVER_TYPES = {"http", "https", "http_jsonrpc", "jsonrpc", "sse", "http_sse"}


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


def execution_mode(server: McpServer) -> str:
    server_type = normalized_server_type(server)
    if server_type == "stdio":
        runtime = server.connection.get("runtime")
        if runtime == "self_hosted":
            return "self_hosted_stdio"
        return "isolated_runtime_stdio"
    if server_type in {"http", "https", "http_jsonrpc", "jsonrpc"}:
        return "remote_http"
    if server_type in {"sse", "http_sse"}:
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
    reasons: list[str] = []
    server_type = normalized_server_type(server)
    if server.status != "active":
        reasons.append("server_inactive")
    if server.health_status == "unhealthy":
        reasons.append("server_unhealthy")
    if mcp_health_check_stale(server, stale_after=stale_after):
        reasons.append("health_check_stale")
    if not tools:
        reasons.append("no_allowed_tools")
    if credential_status == "missing_required":
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
