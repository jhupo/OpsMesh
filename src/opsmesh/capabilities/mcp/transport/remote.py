from __future__ import annotations

import asyncio

from agents.mcp import MCPServerSse, MCPServerStreamableHttp

from opsmesh.capabilities.mcp.execution.contracts import MCPExecutionError
from opsmesh.capabilities.mcp.models import MCPServer
from opsmesh.shared.security.egress import (
    EgressURLPolicy,
    EgressURLValidationError,
    validate_egress_url,
)


def validate_mcp_auth_headers(server: MCPServer, headers: dict[str, str]) -> None:
    auth_method = server.connection.get("auth_method")
    if auth_method in {"static_header", "bearer_token", "credential_ref"} and not headers:
        raise MCPExecutionError(
            "MCP credential did not resolve to headers", code="mcp_auth_missing"
        )
    if auth_method == "bearer_token" and not any(
        name.lower() == "authorization" and value.startswith("Bearer ")
        for name, value in headers.items()
    ):
        raise MCPExecutionError("MCP bearer credential is missing", code="mcp_bearer_missing")


def validate_mcp_url(url: str, *, egress_policy: EgressURLPolicy, transport: str) -> None:
    try:
        validate_egress_url(url, policy=egress_policy)
    except EgressURLValidationError as exc:
        raise MCPExecutionError(
            f"{transport.upper()} MCP server url is invalid",
            code="mcp_server_url_invalid",
        ) from exc


async def list_remote_mcp_tools_async(
    *,
    url: str,
    headers: dict[str, str],
    timeout_seconds: int,
    transport: str,
) -> list[dict[str, object]]:
    """Use SDK discovery and pagination with a product operation deadline."""
    try:
        server = (
            MCPServerStreamableHttp(
                params={"url": url, "headers": headers, "timeout": timeout_seconds},
                client_session_timeout_seconds=timeout_seconds,
                max_retry_attempts=0,
                failure_error_function=None,
            )
            if transport == "http"
            else MCPServerSse(
                params={"url": url, "headers": headers, "timeout": timeout_seconds},
                client_session_timeout_seconds=timeout_seconds,
                max_retry_attempts=0,
                failure_error_function=None,
            )
        )
        async with asyncio.timeout(timeout_seconds), server:
            tools = await server.list_tools()
            if len(tools) > 1_000:
                raise MCPExecutionError("MCP tool catalog exceeds limit", code="mcp_tool_limit")
            return [
                item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in tools
            ]
    except MCPExecutionError:
        raise
    except Exception as exc:
        raise _normalize_remote_exception(exc, transport=transport) from exc


def _normalize_remote_exception(exc: Exception, *, transport: str) -> MCPExecutionError:
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status_code, int):
        return MCPExecutionError(
            f"{transport.upper()} MCP server returned status {status_code}",
            code=f"mcp_{transport}_status_error",
        )
    if isinstance(exc, TimeoutError) or exc.__class__.__name__ in {
        "TimeoutException",
        "ReadTimeout",
        "ConnectTimeout",
    }:
        return MCPExecutionError(
            f"{transport.upper()} MCP server request timed out",
            code=f"mcp_{transport}_timeout",
        )
    return MCPExecutionError(
        f"{transport.upper()} MCP server request failed",
        code=f"mcp_{transport}_request_failed",
    )
