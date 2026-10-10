from __future__ import annotations

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import PaginatedRequestParams

from opsmesh.capabilities.mcp.execution.contracts import McpExecutionError
from opsmesh.capabilities.mcp.models import McpCredentialReference, McpServer
from opsmesh.capabilities.mcp.transport.payloads import string_dict_setting
from opsmesh.shared.security.egress import (
    EgressUrlPolicy,
    EgressUrlValidationError,
    validate_egress_url,
)
from opsmesh.shared.security.secrets import SecretEncryptionService


def credential_headers(
    credential_refs: list[McpCredentialReference],
    *,
    secret_service: SecretEncryptionService | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    for credential in credential_refs:
        if credential.provider != "hosted" or credential.encrypted_secret_payload is None:
            continue
        if secret_service is None:
            raise McpExecutionError(
                "Hosted MCP credential decryption is not configured",
                code="mcp_hosted_credential_unavailable",
            )
        payload = secret_service.decrypt_payload(credential.encrypted_secret_payload)
        headers.update(string_dict_setting(payload, "headers"))
        token = payload.get("bearer_token")
        if isinstance(token, str) and token:
            headers["authorization"] = f"Bearer {token}"
        key = payload.get("api_key")
        if isinstance(key, str) and key:
            name = payload.get("api_key_header", "x-api-key")
            if not isinstance(name, str) or not name:
                raise McpExecutionError("Invalid API key header", code="mcp_credential_invalid")
            headers[name] = key
    return headers


def validate_mcp_auth_headers(server: McpServer, headers: dict[str, str]) -> None:
    auth_method = server.connection.get("auth_method")
    if auth_method in {"static_header", "bearer_token", "credential_ref"} and not headers:
        raise McpExecutionError(
            "MCP credential did not resolve to headers", code="mcp_auth_missing"
        )
    if auth_method == "bearer_token" and not any(
        name.lower() == "authorization" and value.startswith("Bearer ")
        for name, value in headers.items()
    ):
        raise McpExecutionError("MCP bearer credential is missing", code="mcp_bearer_missing")


def validate_mcp_url(url: str, *, egress_policy: EgressUrlPolicy, transport: str) -> None:
    try:
        validate_egress_url(url, policy=egress_policy)
    except EgressUrlValidationError as exc:
        raise McpExecutionError(
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
    """Discover tools through the official MCP client session."""

    try:
        if transport == "http":
            timeout = httpx.Timeout(timeout_seconds)
            async with (
                httpx.AsyncClient(headers=headers, timeout=timeout) as http_client,
                streamable_http_client(
                    url,
                    http_client=http_client,
                ) as (read_stream, write_stream, _),
            ):
                return await _list_tools(read_stream, write_stream)
        async with sse_client(
            url,
            headers=headers,
            timeout=timeout_seconds,
            sse_read_timeout=timeout_seconds,
        ) as (read_stream, write_stream):
            return await _list_tools(read_stream, write_stream)
    except Exception as exc:
        raise _normalize_remote_exception(exc, transport=transport) from exc


async def _list_tools(read_stream: object, write_stream: object) -> list[dict[str, object]]:
    async with ClientSession(read_stream, write_stream) as session:  # type: ignore[arg-type]
        await session.initialize()
        tools: list[dict[str, object]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(100):
            result = await session.list_tools(
                params=PaginatedRequestParams(cursor=cursor) if cursor else None
            )
            tools.extend(
                item.model_dump(mode="json", by_alias=True, exclude_none=True)
                for item in result.tools
            )
            if len(tools) > 1_000:
                raise McpExecutionError("MCP tool catalog exceeds limit", code="mcp_tool_limit")
            cursor = result.nextCursor
            if not cursor:
                return tools
            if cursor in seen_cursors:
                raise McpExecutionError(
                    "MCP tool pagination repeated",
                    code="mcp_pagination_invalid",
                )
            seen_cursors.add(cursor)
    raise McpExecutionError("MCP tool pagination exceeds limit", code="mcp_pagination_limit")


def _normalize_remote_exception(exc: Exception, *, transport: str) -> McpExecutionError:
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status_code, int):
        return McpExecutionError(
            f"{transport.upper()} MCP server returned status {status_code}",
            code=f"mcp_{transport}_status_error",
        )
    if isinstance(exc, TimeoutError) or exc.__class__.__name__ in {
        "TimeoutException",
        "ReadTimeout",
        "ConnectTimeout",
    }:
        return McpExecutionError(
            f"{transport.upper()} MCP server request timed out",
            code=f"mcp_{transport}_timeout",
        )
    return McpExecutionError(
        f"{transport.upper()} MCP server request failed",
        code=f"mcp_{transport}_request_failed",
    )
