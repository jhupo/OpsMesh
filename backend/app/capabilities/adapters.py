from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from backend.app.capabilities.execution import (
    McpExecutionError,
    McpExecutionPending,
    McpToolAdapter,
)
from backend.app.capabilities.models import McpCredentialReference, McpServer
from backend.app.core.resilience import CircuitBreakerConfig, retry_with_circuit
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.egress import (
    MCP_EGRESS_URL_POLICY,
    EgressUrlPolicy,
    EgressUrlValidationError,
    validate_egress_url,
)
from backend.app.self_hosted.service import SelfHostedRuntimeService

MCP_REMOTE_CALL_CIRCUIT_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,
    reset_after_seconds=60,
)
MCP_REMOTE_CALL_MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class McpAdapterResolver:
    secret_service: SecretEncryptionService | None = None

    def resolve(self, server: McpServer) -> McpToolAdapter:
        server_type = server.server_type.lower().strip()
        if server_type in {"http", "https", "http_jsonrpc", "jsonrpc"}:
            return HttpJsonRpcMcpToolAdapter(secret_service=self.secret_service)
        if server_type in {"sse", "http_sse"}:
            return SseMcpToolAdapter(secret_service=self.secret_service)
        if server_type == "hosted":
            return HostedMcpToolAdapter(secret_service=self.secret_service)
        if server_type == "stdio":
            return UnsupportedMcpToolAdapter(server_type=server_type)
        return UnsupportedMcpToolAdapter(server_type=server.server_type)


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
        url = _string_setting(server.connection, "url") or _string_setting(
            server.connection,
            "endpoint",
        )
        if not url:
            raise McpExecutionError("HTTP MCP server is missing url", code="mcp_server_url_missing")
        _validate_mcp_url(url, egress_policy=self._egress_policy, transport="http")

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
        raw_body = _call_remote_mcp(
            request=request,
            timeout_seconds=timeout_seconds,
            circuit_key=_mcp_circuit_key(url, transport="http"),
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
        url = _string_setting(server.connection, "url") or _string_setting(
            server.connection,
            "endpoint",
        )
        if not url:
            raise McpExecutionError("SSE MCP server is missing url", code="mcp_server_url_missing")
        _validate_mcp_url(url, egress_policy=self._egress_policy, transport="sse")

        headers = {
            "content-type": "application/json",
            "accept": "text/event-stream",
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
        raw_body = _call_remote_mcp(
            request=request,
            timeout_seconds=timeout_seconds,
            circuit_key=_mcp_circuit_key(url, transport="sse"),
            transport="sse",
        )

        return _result_from_sse_body(raw_body)


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
        transport = (_string_setting(server.connection, "transport") or "").lower().strip()
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


class DockerRuntimeStdioMcpToolAdapter:
    def __init__(self, *, runtime_manager: RuntimeManager, runtime: WorkspaceRuntime) -> None:
        self._runtime_manager = runtime_manager
        self._runtime = runtime

    def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        _ = credential_refs, timeout_seconds
        command = _stdio_command(server.connection)
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": _string_setting(server.connection, "method") or "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }
        record = self._runtime_manager.execute_command(
            workspace_id=server.workspace_id,
            runtime=self._runtime,
            command=[*command, json.dumps(payload, ensure_ascii=False)],
        )
        if record.status != "completed" or record.exit_code != 0:
            raise McpExecutionError(
                "Docker runtime MCP stdio command failed",
                code="mcp_stdio_runtime_failed",
            )
        return _result_from_jsonrpc_body(record.stdout, transport="stdio")


class SelfHostedStdioMcpToolAdapter:
    def __init__(
        self,
        *,
        service: SelfHostedRuntimeService,
        runtime: WorkspaceRuntime,
        agent_run_id: UUID,
    ) -> None:
        self._service = service
        self._runtime = runtime
        self._agent_run_id = agent_run_id

    def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        _ = credential_refs, timeout_seconds
        command = _stdio_command(server.connection)
        payload = {
            "transport": "stdio",
            "command": command,
            "jsonrpc": {
                "jsonrpc": "2.0",
                "id": str(uuid4()),
                "method": _string_setting(server.connection, "method") or "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            },
        }
        job = self._service.create_mcp_job(
            workspace_id=server.workspace_id,
            runtime_id=self._runtime.id,
            agent_run_id=self._agent_run_id,
            mcp_server_id=server.id,
            tool_name=tool_name,
            request_payload=payload,
        )
        raise McpExecutionPending(
            "MCP tool is queued for self-hosted runtime execution",
            code="mcp_self_hosted_job_queued",
            response={
                "mcp_job_id": str(job.id),
                "runtime_id": str(self._runtime.id),
                "transport": "stdio",
            },
        )


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


def _validate_mcp_url(url: str, *, egress_policy: EgressUrlPolicy, transport: str) -> None:
    try:
        validate_egress_url(url, policy=egress_policy)
    except EgressUrlValidationError as exc:
        raise McpExecutionError(
            f"{transport.upper()} MCP server url is invalid",
            code="mcp_server_url_invalid",
        ) from exc


def _call_remote_mcp(
    *,
    request: Request,
    timeout_seconds: int,
    circuit_key: str,
    transport: str,
) -> bytes:
    return retry_with_circuit(
        key=circuit_key,
        func=lambda: _read_url(request, timeout_seconds=timeout_seconds, transport=transport),
        max_attempts=MCP_REMOTE_CALL_MAX_ATTEMPTS,
        circuit_config=MCP_REMOTE_CALL_CIRCUIT_CONFIG,
        should_retry=_is_retryable_mcp_error,
    )


def _read_url(request: Request, *, timeout_seconds: int, transport: str) -> bytes:
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


def _is_retryable_mcp_error(exc: Exception) -> bool:
    return isinstance(exc, McpExecutionError) and exc.code in {
        "mcp_http_request_failed",
        "mcp_http_timeout",
        "mcp_http_retryable_status_error",
        "mcp_sse_request_failed",
        "mcp_sse_timeout",
        "mcp_sse_retryable_status_error",
    }


def _mcp_circuit_key(url: str, *, transport: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        host = "invalid-url"
        path = ""
    else:
        host = parsed.netloc or "missing-host"
        path = parsed.path or "/"
    return f"mcp:{transport}:{host}:{path}"


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


def _stdio_command(connection: dict[str, object]) -> list[str]:
    raw_command = connection.get("command")
    if isinstance(raw_command, list) and raw_command:
        command = [item for item in raw_command if isinstance(item, str) and item]
        if command:
            return command
    if isinstance(raw_command, str) and raw_command.strip():
        command = [raw_command.strip()]
        args = connection.get("args")
        if isinstance(args, list):
            command.extend(item for item in args if isinstance(item, str) and item)
        return command
    raise McpExecutionError(
        "Stdio MCP server is missing command",
        code="mcp_stdio_command_missing",
    )


def _result_from_jsonrpc_body(raw_body: bytes | str, *, transport: str) -> dict[str, object]:
    if isinstance(raw_body, bytes):
        try:
            raw_body = raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise McpExecutionError(
                f"{transport.upper()} MCP server returned invalid UTF-8",
                code=f"mcp_{transport}_invalid_encoding",
            ) from exc
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise McpExecutionError(
            f"{transport.upper()} MCP server returned invalid JSON",
            code=f"mcp_{transport}_invalid_json",
        ) from exc
    if not isinstance(body, dict):
        raise McpExecutionError(
            f"{transport.upper()} MCP server returned an invalid JSON-RPC envelope",
            code=f"mcp_{transport}_invalid_envelope",
        )
    error = body.get("error")
    if isinstance(error, dict):
        raise McpExecutionError(
            "Remote MCP tool failed",
            code="mcp_remote_error",
        )
    result = body.get("result")
    return result if isinstance(result, dict) else {"result": _jsonable(result)}


def _result_from_sse_body(raw_body: bytes) -> dict[str, object]:
    try:
        body = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise McpExecutionError(
            "SSE MCP server returned invalid UTF-8",
            code="mcp_sse_invalid_encoding",
        ) from exc

    for data in _sse_data_messages(body):
        if not data or data == "[DONE]":
            continue
        try:
            envelope = json.loads(data)
        except json.JSONDecodeError as exc:
            raise McpExecutionError(
                "SSE MCP server returned invalid JSON",
                code="mcp_sse_invalid_json",
            ) from exc
        if not isinstance(envelope, dict):
            raise McpExecutionError(
                "SSE MCP server returned an invalid JSON-RPC envelope",
                code="mcp_sse_invalid_envelope",
            )
        error = envelope.get("error")
        if isinstance(error, dict):
            raise McpExecutionError(
                "Remote MCP tool failed",
                code="mcp_remote_error",
            )
        if "result" in envelope:
            result = envelope["result"]
            return result if isinstance(result, dict) else {"result": _jsonable(result)}

    raise McpExecutionError(
        "SSE MCP server did not return a result",
        code="mcp_sse_no_result",
    )


def _sse_data_messages(body: str) -> list[str]:
    messages: list[str] = []
    data_lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                messages.append("\n".join(data_lines))
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
    if data_lines:
        messages.append("\n".join(data_lines))
    return messages
