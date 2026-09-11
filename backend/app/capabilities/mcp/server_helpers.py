from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.capabilities.models import McpServer
from backend.app.security.redaction import redact_sensitive_text


def require_mcp_server(session: Session, workspace_id: UUID, server_id: UUID) -> McpServer:
    server = session.get(McpServer, server_id)
    if server is None or server.workspace_id != workspace_id:
        raise ValueError("MCP server not found")
    return server


def mcp_health_error(health_status: str, error_code: str | None) -> str | None:
    if health_status == "healthy":
        return None
    normalized = error_code.strip() if isinstance(error_code, str) else ""
    if normalized:
        return redact_sensitive_text(normalized)
    if health_status == "unhealthy":
        return "health_check_failed"
    return None
