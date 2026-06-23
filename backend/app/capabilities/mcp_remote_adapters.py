from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from backend.app.capabilities.mcp_adapter_payloads import (
    jsonable,
    result_from_sse_body,
    string_dict_setting,
    string_setting,
    tool_call_payload,
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


class HttpJsonRpcMcpToolAdapter:
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
            "content-type": "application/json",
            "accept": "application/json",
            **string_dict_setting(server.connection, "headers"),
            **self._credential_headers(credential_refs),
        }
        payload = tool_call_payload(
            method=string_setting(server.connection, "method"),
            tool_name=tool_name,
            arguments=arguments,
        )
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        raw_body = call_remote_mcp(
            request=request,
            timeout_seconds=timeout_seconds,
            circuit_key=mcp_circuit_key(url, transport="http"),
            transport="http",
        )

        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise McpExecutionError(
                "HTTP MCP server returned invalid JSON",
                code="mcp_http_invalid_json",
            ) from exc
        if not isinstance(body, dict):
            raise McpExecutionError(
                "HTTP MCP server returned an invalid JSON-RPC envelope",
                code="mcp_http_invalid_envelope",
            )
        error = body.get("error")
        if isinstance(error, dict):
            raise McpExecutionError(
                "Remote MCP tool failed",
                code="mcp_remote_error",
            )
        result = body.get("result")
        return result if isinstance(result, dict) else {"result": jsonable(result)}

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


class SseMcpToolAdapter(HttpJsonRpcMcpToolAdapter):
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
            "content-type": "application/json",
            "accept": "text/event-stream",
            **string_dict_setting(server.connection, "headers"),
            **self._credential_headers(credential_refs),
        }
        payload = tool_call_payload(
            method=string_setting(server.connection, "method"),
            tool_name=tool_name,
            arguments=arguments,
        )
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        raw_body = call_remote_mcp(
            request=request,
            timeout_seconds=timeout_seconds,
            circuit_key=mcp_circuit_key(url, transport="sse"),
            transport="sse",
        )

        return result_from_sse_body(raw_body)


class HostedMcpToolAdapter:
    def __init__(
        self,
        *,
        secret_service: SecretEncryptionService | None = None,
        egress_policy: EgressUrlPolicy = MCP_EGRESS_URL_POLICY,
    ) -> None:
        self._http_adapter = HttpJsonRpcMcpToolAdapter(
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
        if transport in {"http", "https", "http_jsonrpc", "jsonrpc"}:
            return self._http_adapter.call(
                server=server,
                tool_name=tool_name,
                arguments=arguments,
                credential_refs=credential_refs,
                timeout_seconds=timeout_seconds,
            )
        if transport in {"sse", "http_sse"}:
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
    request: Request,
    timeout_seconds: int,
    circuit_key: str,
    transport: str,
) -> bytes:
    return retry_with_circuit(
        key=circuit_key,
        func=lambda: read_url(request, timeout_seconds=timeout_seconds, transport=transport),
        max_attempts=MCP_REMOTE_CALL_MAX_ATTEMPTS,
        circuit_config=MCP_REMOTE_CALL_CIRCUIT_CONFIG,
        should_retry=is_retryable_mcp_error,
    )


def read_url(request: Request, *, timeout_seconds: int, transport: str) -> bytes:
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            return response.read()
    except HTTPError as exc:
        if exc.code == 408 or exc.code == 429 or exc.code >= 500:
            code = f"mcp_{transport}_retryable_status_error"
        else:
            code = f"mcp_{transport}_status_error"
        raise McpExecutionError(
            f"{transport.upper()} MCP server returned status {exc.code}",
            code=code,
        ) from exc
    except URLError as exc:
        raise McpExecutionError(
            f"{transport.upper()} MCP server request failed",
            code=f"mcp_{transport}_request_failed",
        ) from exc
    except TimeoutError as exc:
        raise McpExecutionError(
            f"{transport.upper()} MCP server request timed out",
            code=f"mcp_{transport}_timeout",
        ) from exc


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
