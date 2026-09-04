from __future__ import annotations

import asyncio
from datetime import timedelta
from urllib.parse import urlparse

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from backend.app.capabilities.mcp_adapter_payloads import (
    string_dict_setting,
    string_setting,
)
from backend.app.capabilities.mcp_execution_types import McpExecutionError
from backend.app.capabilities.models import McpCredentialReference, McpServer
from backend.app.core.resilience import CircuitBreakerConfig, retry_with_circuit
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.egress import (
    MCP_EGRESS_URL_POLICY,
    EgressUrlPolicy,
    EgressUrlValidationError,
    validate_egress_url,
)

MCP_REMOTE_CALL_CIRCUIT_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,
    reset_after_seconds=60,
)
MCP_REMOTE_CALL_MAX_ATTEMPTS = 2


class StreamableHttpMcpToolAdapter:
    def __init__(
        self,
        *,
        secret_service: SecretEncryptionService | None = None,
        egress_policy: EgressUrlPolicy = MCP_EGRESS_URL_POLICY,
    ) -> None:
        self._secret_service = secret_service
        self._egress_policy = egress_policy

    def call(
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
        validate_mcp_url(url, egress_policy=self._egress_policy, transport="http")
        headers = {
            **string_dict_setting(server.connection, "headers"),
            **self._credential_headers(credential_refs),
        }
        return call_remote_mcp(
            url=url,
            headers=headers,
            tool_name=tool_name,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            circuit_key=mcp_circuit_key(url, transport="http"),
            transport="http",
        )

    def _credential_headers(self, credential_refs: list[McpCredentialReference]) -> dict[str, str]:
        headers: dict[str, str] = {}
        for credential in credential_refs:
            if credential.provider == "hosted":
                headers.update(self._hosted_credential_headers(credential))
                continue
            if credential.provider == "static_header" and credential.external_ref:
                header_name, _, header_value = credential.external_ref.partition(":")
                if header_name.strip() and header_value.strip():
                    headers[header_name.strip()] = header_value.strip()
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


class SseMcpToolAdapter(StreamableHttpMcpToolAdapter):
    def call(
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
            raise McpExecutionError("SSE MCP server is missing url", code="mcp_server_url_missing")
        validate_mcp_url(url, egress_policy=self._egress_policy, transport="sse")
        headers = {
            **string_dict_setting(server.connection, "headers"),
            **self._credential_headers(credential_refs),
        }
        return call_remote_mcp(
            url=url,
            headers=headers,
            tool_name=tool_name,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            circuit_key=mcp_circuit_key(url, transport="sse"),
            transport="sse",
        )


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

    def call(
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
            return self._http_adapter.call(
                server=server,
                tool_name=tool_name,
                arguments=arguments,
                credential_refs=credential_refs,
                timeout_seconds=timeout_seconds,
            )
        if transport == "sse":
            return self._sse_adapter.call(
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


def call_remote_mcp(
    *,
    url: str,
    headers: dict[str, str],
    tool_name: str,
    arguments: dict[str, object],
    timeout_seconds: int,
    circuit_key: str,
    transport: str,
) -> dict[str, object]:
    return retry_with_circuit(
        key=circuit_key,
        func=lambda: asyncio.run(
            call_remote_mcp_async(
                url=url,
                headers=headers,
                tool_name=tool_name,
                arguments=arguments,
                timeout_seconds=timeout_seconds,
                transport=transport,
            )
        ),
        max_attempts=MCP_REMOTE_CALL_MAX_ATTEMPTS,
        circuit_config=MCP_REMOTE_CALL_CIRCUIT_CONFIG,
        should_retry=is_retryable_mcp_error,
    )


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
        code = (
            f"mcp_{transport}_retryable_status_error"
            if status_code == 408 or status_code == 429 or status_code >= 500
            else f"mcp_{transport}_status_error"
        )
        return McpExecutionError(
            f"{transport.upper()} MCP server returned status {status_code}",
            code=code,
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


def is_retryable_mcp_error(exc: Exception) -> bool:
    return isinstance(exc, McpExecutionError) and exc.code in {
        "mcp_http_request_failed",
        "mcp_http_timeout",
        "mcp_http_retryable_status_error",
        "mcp_sse_request_failed",
        "mcp_sse_timeout",
        "mcp_sse_retryable_status_error",
    }


def mcp_circuit_key(url: str, *, transport: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        host = "invalid-url"
        path = ""
    else:
        host = parsed.netloc or "missing-host"
        path = parsed.path or "/"
    return f"mcp:{transport}:{host}:{path}"
