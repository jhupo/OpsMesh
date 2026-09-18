from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, PaginatedRequestParams

from backend.app.core.security.egress import (
    MCP_EGRESS_URL_POLICY,
    EgressUrlPolicy,
    EgressUrlValidationError,
    validate_egress_url,
)
from backend.app.core.security.secrets import SecretEncryptionService
from backend.app.domains.capabilities.mcp.execution.contracts import McpExecutionError
from backend.app.domains.capabilities.mcp.models import (
    McpCredentialReference,
    McpServer,
)
from backend.app.domains.capabilities.mcp.transport.payloads import (
    string_dict_setting,
    string_setting,
)


class BaseRemoteMcpToolAdapter(ABC):
    @property
    @abstractmethod
    def transport(self) -> str: ...

    def __init__(
        self,
        *,
        secret_service: SecretEncryptionService | None = None,
        egress_policy: EgressUrlPolicy = MCP_EGRESS_URL_POLICY,
    ) -> None:
        self._secret_service = secret_service
        self._egress_policy = egress_policy

    async def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        url = string_setting(server.connection, "url") or string_setting(
            server.connection,
            "endpoint",
        )
        if not url:
            raise McpExecutionError("HTTP MCP server is missing url", code="mcp_server_url_missing")
        validate_mcp_url(url, egress_policy=self._egress_policy, transport=self.transport)
        resolved_credentials = self._credential_headers(credential_refs)
        validate_mcp_auth_headers(server, resolved_credentials)
        headers = {
            **string_dict_setting(server.connection, "headers"),
            **resolved_credentials,
        }
        return await call_remote_mcp_async(
            url=url,
            headers=headers,
            tool_name=tool_name,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            transport=self.transport,
        )

    def _credential_headers(self, credential_refs: list[McpCredentialReference]) -> dict[str, str]:
        headers: dict[str, str] = {}
        for credential in credential_refs:
            if credential.provider == "hosted":
                headers.update(self._hosted_credential_headers(credential))
        return headers

    def _hosted_credential_headers(self, credential: McpCredentialReference) -> dict[str, str]:
        if credential.encrypted_secret_payload is None:
            return {}
        if self._secret_service is None:
            raise McpExecutionError(
                "Hosted MCP credential decryption is not configured",
                code="mcp_hosted_credential_unavailable",
            )
        payload = self._secret_service.decrypt_payload(credential.encrypted_secret_payload)
        headers = string_dict_setting(payload, "headers")
        bearer_token = payload.get("bearer_token")
        if isinstance(bearer_token, str) and bearer_token:
            headers["authorization"] = f"Bearer {bearer_token}"
        api_key = payload.get("api_key")
        if isinstance(api_key, str) and api_key:
            header_name = payload.get("api_key_header")
            headers[str(header_name) if isinstance(header_name, str) else "x-api-key"] = api_key
        return headers


def credential_headers(
    credential_refs: list[McpCredentialReference],
    *,
    secret_service: SecretEncryptionService | None = None,
) -> dict[str, str]:
    """Resolve discovery-time headers using the same credential rules as execution."""

    return StreamableHttpMcpToolAdapter(secret_service=secret_service)._credential_headers(
        credential_refs
    )


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


class StreamableHttpMcpToolAdapter(BaseRemoteMcpToolAdapter):
    @property
    def transport(self) -> str:
        return "http"


class SseMcpToolAdapter(BaseRemoteMcpToolAdapter):
    @property
    def transport(self) -> str:
        return "sse"


class HostedMcpToolAdapter:
    def __init__(
        self,
        *,
        secret_service: SecretEncryptionService | None = None,
        egress_policy: EgressUrlPolicy = MCP_EGRESS_URL_POLICY,
    ) -> None:
        self._http_adapter = StreamableHttpMcpToolAdapter(
            secret_service=secret_service,
            egress_policy=egress_policy,
        )
        self._sse_adapter = SseMcpToolAdapter(
            secret_service=secret_service,
            egress_policy=egress_policy,
        )

    async def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        transport = (string_setting(server.connection, "transport") or "").lower().strip()
        if transport == "streamable_http":
            return await self._http_adapter.call(
                server=server,
                tool_name=tool_name,
                arguments=arguments,
                credential_refs=credential_refs,
                timeout_seconds=timeout_seconds,
            )
        if transport == "sse":
            return await self._sse_adapter.call(
                server=server,
                tool_name=tool_name,
                arguments=arguments,
                credential_refs=credential_refs,
                timeout_seconds=timeout_seconds,
            )
        raise McpExecutionError(
            "Hosted MCP server must declare a supported remote transport",
            code="mcp_hosted_transport_unsupported",
        )


def validate_mcp_url(url: str, *, egress_policy: EgressUrlPolicy, transport: str) -> None:
    try:
        validate_egress_url(url, policy=egress_policy)
    except EgressUrlValidationError as exc:
        raise McpExecutionError(
            f"{transport.upper()} MCP server url is invalid",
            code="mcp_server_url_invalid",
        ) from exc


async def call_remote_mcp_async(
    *,
    url: str,
    headers: dict[str, str],
    tool_name: str,
    arguments: dict[str, object],
    timeout_seconds: int,
    transport: str,
) -> dict[str, object]:
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
                return await _call_tool(
                    read_stream,
                    write_stream,
                    tool_name,
                    arguments,
                    timeout_seconds,
                )
        async with sse_client(
            url,
            headers=headers,
            timeout=timeout_seconds,
            sse_read_timeout=timeout_seconds,
        ) as (read_stream, write_stream):
            return await _call_tool(
                read_stream,
                write_stream,
                tool_name,
                arguments,
                timeout_seconds,
            )
    except McpExecutionError:
        raise
    except Exception as exc:
        raise _normalize_remote_exception(exc, transport=transport) from exc


async def _call_tool(
    read_stream: object,
    write_stream: object,
    tool_name: str,
    arguments: dict[str, object],
    timeout_seconds: int,
) -> dict[str, object]:
    async with ClientSession(read_stream, write_stream) as session:  # type: ignore[arg-type]
        await session.initialize()
        result = await session.call_tool(
            tool_name,
            arguments=arguments,
            read_timeout_seconds=timedelta(seconds=timeout_seconds),
        )
    return _result_payload(result)


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


def _result_payload(result: CallToolResult) -> dict[str, object]:
    if result.isError:
        raise McpExecutionError("Remote MCP tool failed", code="mcp_remote_error")
    if isinstance(result.structuredContent, dict):
        return {
            str(key): value
            for key, value in result.structuredContent.items()
            if isinstance(key, str)
        }
    return {
        "content": [
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in result.content
        ]
    }


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
