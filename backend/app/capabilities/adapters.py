from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from backend.app.capabilities.execution import McpExecutionError, McpToolAdapter
from backend.app.capabilities.models import McpCredentialReference, McpServer
from backend.app.secrets.service import SecretEncryptionService


@dataclass(frozen=True)
class McpAdapterResolver:
    secret_service: SecretEncryptionService | None = None

    def resolve(self, server: McpServer) -> McpToolAdapter:
        server_type = server.server_type.lower().strip()
        if server_type in {"http", "https", "http_jsonrpc", "jsonrpc"}:
            return HttpJsonRpcMcpToolAdapter(secret_service=self.secret_service)
        if server_type in {"stdio", "sse", "hosted"}:
            return UnsupportedMcpToolAdapter(server_type=server_type)
        return UnsupportedMcpToolAdapter(server_type=server.server_type)


class HttpJsonRpcMcpToolAdapter:
    def __init__(self, *, secret_service: SecretEncryptionService | None = None) -> None:
        self._secret_service = secret_service

    def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        url = _string_setting(server.connection, "url") or _string_setting(
            server.connection,
            "endpoint",
        )
        if not url:
            raise McpExecutionError("HTTP MCP server is missing url", code="mcp_server_url_missing")
        if not url.lower().startswith(("https://", "http://")):
            raise McpExecutionError("HTTP MCP server url is invalid", code="mcp_server_url_invalid")

        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            **_string_dict_setting(server.connection, "headers"),
            **self._credential_headers(credential_refs),
        }
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": _string_setting(server.connection, "method") or "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                raw_body = response.read()
        except HTTPError as exc:
            raise McpExecutionError(
                f"HTTP MCP server returned status {exc.code}",
                code="mcp_http_status_error",
            ) from exc
        except URLError as exc:
            raise McpExecutionError(
                "HTTP MCP server request failed",
                code="mcp_http_request_failed",
            ) from exc
        except TimeoutError as exc:
            raise McpExecutionError(
                "HTTP MCP server request timed out",
                code="mcp_http_timeout",
            ) from exc

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
        return result if isinstance(result, dict) else {"result": _jsonable(result)}

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
        headers = _string_dict_setting(payload, "headers")
        bearer_token = payload.get("bearer_token")
        if isinstance(bearer_token, str) and bearer_token:
            headers["authorization"] = f"Bearer {bearer_token}"
        api_key = payload.get("api_key")
        if isinstance(api_key, str) and api_key:
            header_name = payload.get("api_key_header")
            headers[str(header_name) if isinstance(header_name, str) else "x-api-key"] = api_key
        return headers


@dataclass(frozen=True)
class UnsupportedMcpToolAdapter:
    server_type: str

    def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        raise McpExecutionError(
            f"MCP server type is not configured for direct execution: {self.server_type}",
            code="mcp_adapter_unsupported",
        )


def _string_setting(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _string_dict_setting(payload: dict[str, object], key: str) -> dict[str, str]:
    value = payload.get(key)
    if not isinstance(value, dict):
        return {}
    return {
        str(item_key).lower(): item_value
        for item_key, item_value in value.items()
        if isinstance(item_key, str) and isinstance(item_value, str)
    }


def _jsonable(value: Any) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)
