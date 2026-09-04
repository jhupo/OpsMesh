from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from backend.app.capabilities.mcp_adapter_resolver import McpAdapterResolver
from backend.app.capabilities.mcp_execution_types import McpExecutionError
from backend.app.capabilities.mcp_remote_adapters import (
    HostedMcpToolAdapter,
    SseMcpToolAdapter,
    StreamableHttpMcpToolAdapter,
)
from backend.app.capabilities.mcp_stdio_adapters import DockerRuntimeStdioMcpToolAdapter
from backend.app.capabilities.mcp_unsupported_adapter import UnsupportedMcpToolAdapter
from backend.app.capabilities.models import McpCredentialReference, McpServer
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.runtime_manager.contracts import RuntimeCommandResult
from backend.app.runtimes.models import RuntimeCommand, WorkspaceRuntime
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.egress import EgressUrlPolicy


def test_streamable_http_mcp_adapter_uses_official_client_session(monkeypatch) -> None:
    sdk = _FakeMcpSdk(
        [_FakeCallToolResult(content=[_FakeContent({"type": "text", "text": "ok"})])]
    )
    monkeypatch.setattr(
        "backend.app.capabilities.mcp_remote_adapters.streamable_http_client",
        sdk.streamable_http_client,
    )
    monkeypatch.setattr(
        "backend.app.capabilities.mcp_remote_adapters.ClientSession",
        sdk.client_session,
    )
    secret_service = SecretEncryptionService(secret="test-secret", key_id="test")
    encrypted = secret_service.encrypt_payload(
        {
            "bearer_token": "secret-token",
            "headers": {"x-tenant": "acme"},
        }
    )
    credential = McpCredentialReference(
        workspace_id=uuid4(),
        name="hosted",
        provider="hosted",
        external_ref="",
        encrypted_secret_payload=encrypted.ciphertext,
        secret_fingerprint=encrypted.fingerprint,
        encryption_key_id=encrypted.key_id,
    )

    response = StreamableHttpMcpToolAdapter(
        secret_service=secret_service,
        egress_policy=_local_test_egress_policy(),
    ).call(
        server=McpServer(
            workspace_id=uuid4(),
            name="http-tools",
            server_type="streamable_http",
            connection={
                "url": "https://mcp.example.test/mcp",
                "headers": {"x-static": "yes"},
            },
        ),
        tool_name="generate_image",
        arguments={"prompt": "mountain"},
        credential_refs=[credential],
        timeout_seconds=5,
    )

    assert response == {"content": [{"type": "text", "text": "ok"}]}
    assert sdk.transport_calls == [
        {
            "transport": "streamable_http",
            "url": "https://mcp.example.test/mcp",
            "headers": {
                "x-static": "yes",
                "authorization": "Bearer secret-token",
                "x-tenant": "acme",
            },
            "timeout": 5,
        }
    ]
    assert sdk.initialize_calls == 1
    assert sdk.tool_calls == [
        {
            "name": "generate_image",
            "arguments": {"prompt": "mountain"},
            "timeout_seconds": 5,
        }
    ]


def test_streamable_http_mcp_adapter_retries_retryable_status(monkeypatch) -> None:
    sdk = _FakeMcpSdk(
        [
            _FakeStatusError(500),
            _FakeCallToolResult(structured_content={"ok": True}),
        ]
    )
    monkeypatch.setattr(
        "backend.app.capabilities.mcp_remote_adapters.streamable_http_client",
        sdk.streamable_http_client,
    )
    monkeypatch.setattr(
        "backend.app.capabilities.mcp_remote_adapters.ClientSession",
        sdk.client_session,
    )
    response = StreamableHttpMcpToolAdapter(
        egress_policy=_local_test_egress_policy(),
    ).call(
        server=McpServer(
            workspace_id=uuid4(),
            name="http-tools",
            server_type="streamable_http",
            connection={"url": "https://retry.example.test/mcp"},
        ),
        tool_name="generate_image",
        arguments={"prompt": "mountain"},
        credential_refs=[],
        timeout_seconds=5,
    )

    assert response == {"ok": True}
    assert len(sdk.tool_calls) == 2


def test_streamable_http_mcp_adapter_sanitizes_remote_errors(monkeypatch) -> None:
    sdk = _FakeMcpSdk([_FakeCallToolResult(is_error=True)])
    monkeypatch.setattr(
        "backend.app.capabilities.mcp_remote_adapters.streamable_http_client",
        sdk.streamable_http_client,
    )
    monkeypatch.setattr(
        "backend.app.capabilities.mcp_remote_adapters.ClientSession",
        sdk.client_session,
    )
    try:
        StreamableHttpMcpToolAdapter(egress_policy=_local_test_egress_policy()).call(
            server=McpServer(
                workspace_id=uuid4(),
                name="http-tools",
                server_type="streamable_http",
                connection={"url": "https://errors.example.test/mcp"},
            ),
            tool_name="generate_image",
            arguments={"prompt": "mountain"},
            credential_refs=[],
            timeout_seconds=5,
        )
    except McpExecutionError as exc:
        assert exc.code == "mcp_remote_error"
        assert str(exc) == "Remote MCP tool failed"
        assert "secret-token" not in str(exc)
    else:
        raise AssertionError("Expected remote MCP error")


def test_hosted_mcp_adapter_delegates_to_official_remote_http_transport(monkeypatch) -> None:
    sdk = _FakeMcpSdk([_FakeCallToolResult(structured_content={"ok": True})])
    monkeypatch.setattr(
        "backend.app.capabilities.mcp_remote_adapters.streamable_http_client",
        sdk.streamable_http_client,
    )
    monkeypatch.setattr(
        "backend.app.capabilities.mcp_remote_adapters.ClientSession",
        sdk.client_session,
    )
    secret_service = SecretEncryptionService(secret="test-secret", key_id="test")
    encrypted = secret_service.encrypt_payload({"api_key": "secret-key"})
    credential = McpCredentialReference(
        workspace_id=uuid4(),
        name="hosted",
        provider="hosted",
        external_ref="",
        encrypted_secret_payload=encrypted.ciphertext,
        secret_fingerprint=encrypted.fingerprint,
        encryption_key_id=encrypted.key_id,
    )

    response = HostedMcpToolAdapter(
        secret_service=secret_service,
        egress_policy=_local_test_egress_policy(),
    ).call(
        server=McpServer(
            workspace_id=uuid4(),
            name="hosted-tools",
            server_type="hosted",
            connection={
                "transport": "streamable_http",
                "url": "https://hosted.example.test/mcp",
            },
        ),
        tool_name="generate_image",
        arguments={"prompt": "mountain"},
        credential_refs=[credential],
        timeout_seconds=5,
    )

    assert response == {"ok": True}
    assert sdk.transport_calls[0]["headers"]["x-api-key"] == "secret-key"


def test_hosted_mcp_adapter_blocks_missing_remote_transport() -> None:
    try:
        HostedMcpToolAdapter().call(
            server=McpServer(
                workspace_id=uuid4(),
                name="hosted-tools",
                server_type="hosted",
                connection={"command": "mcp-server"},
            ),
            tool_name="generate_image",
            arguments={"prompt": "mountain"},
            credential_refs=[],
            timeout_seconds=5,
        )
    except McpExecutionError as exc:
        assert exc.code == "mcp_hosted_transport_unsupported"
    else:
        raise AssertionError("Expected hosted MCP without remote transport to be blocked")


def test_http_mcp_adapter_blocks_private_egress_before_request() -> None:
    try:
        StreamableHttpMcpToolAdapter().call(
            server=McpServer(
                workspace_id=uuid4(),
                name="http-tools",
                server_type="streamable_http",
                connection={"url": "http://127.0.0.1/mcp"},
            ),
            tool_name="generate_image",
            arguments={"prompt": "mountain"},
            credential_refs=[],
            timeout_seconds=5,
        )
    except McpExecutionError as exc:
        assert exc.code == "mcp_server_url_invalid"
    else:
        raise AssertionError("Expected private MCP egress URL to be blocked")


def test_docker_runtime_stdio_mcp_adapter_executes_inside_runtime_manager() -> None:
    workspace_id = uuid4()
    runtime = WorkspaceRuntime(
        id=uuid4(),
        workspace_id=workspace_id,
        name="team-runtime",
        docker_container_id="container-123",
        limits={},
    )
    runtime_manager = RecordingRuntimeManager(
        RuntimeCommandResult(
            exit_code=0,
            stdout=json.dumps({"jsonrpc": "2.0", "id": "1", "result": {"ok": True}}),
            stderr="",
        )
    )

    response = DockerRuntimeStdioMcpToolAdapter(
        runtime_manager=runtime_manager,
        runtime=runtime,
    ).call(
        server=McpServer(
            workspace_id=workspace_id,
            name="stdio-tools",
            server_type="stdio",
            connection={"command": ["mcp-server", "--stdio"]},
        ),
        tool_name="generate_image",
        arguments={"prompt": "mountain"},
        credential_refs=[],
        timeout_seconds=5,
    )

    assert response == {"ok": True}
    assert runtime_manager.calls[0]["workspace_id"] == workspace_id
    assert runtime_manager.calls[0]["runtime"] is runtime
    command = runtime_manager.calls[0]["command"]
    assert command[:2] == ["mcp-server", "--stdio"]
    payload = json.loads(command[2])
    assert payload["method"] == "tools/call"
    assert payload["params"] == {
        "name": "generate_image",
        "arguments": {"prompt": "mountain"},
    }


def test_docker_runtime_stdio_mcp_adapter_sanitizes_command_failure() -> None:
    workspace_id = uuid4()
    runtime_manager = RecordingRuntimeManager(
        RuntimeCommandResult(exit_code=2, stdout="", stderr="secret stderr")
    )

    try:
        DockerRuntimeStdioMcpToolAdapter(
            runtime_manager=runtime_manager,
            runtime=WorkspaceRuntime(
                id=uuid4(),
                workspace_id=workspace_id,
                name="team-runtime",
                docker_container_id="container-123",
                limits={},
            ),
        ).call(
            server=McpServer(
                workspace_id=workspace_id,
                name="stdio-tools",
                server_type="stdio",
                connection={"command": "mcp-server"},
            ),
            tool_name="generate_image",
            arguments={"prompt": "mountain"},
            credential_refs=[],
            timeout_seconds=5,
        )
    except McpExecutionError as exc:
        assert exc.code == "mcp_stdio_runtime_failed"
        assert "secret" not in str(exc)
    else:
        raise AssertionError("Expected failed stdio runtime command to be normalized")


def test_mcp_adapter_resolver_selects_remote_adapters_and_blocks_unsafe_direct_stdio() -> None:
    resolver = McpAdapterResolver()

    http_adapter = resolver.resolve(
        McpServer(
            workspace_id=uuid4(),
            name="http-tools",
            server_type="streamable_http",
            connection={"url": "https://example.test/mcp"},
        )
    )
    sse_adapter = resolver.resolve(
        McpServer(
            workspace_id=uuid4(),
            name="sse-tools",
            server_type="sse",
            connection={"url": "https://example.test/sse"},
        )
    )
    hosted_adapter = resolver.resolve(
        McpServer(
            workspace_id=uuid4(),
            name="hosted-tools",
            server_type="hosted",
            connection={"transport": "streamable_http", "url": "https://example.test/mcp"},
        )
    )
    stdio_adapter = resolver.resolve(
        McpServer(
            workspace_id=uuid4(),
            name="stdio-tools",
            server_type="stdio",
            connection={"command": "mcp-server"},
        )
    )

    assert isinstance(http_adapter, StreamableHttpMcpToolAdapter)
    assert isinstance(sse_adapter, SseMcpToolAdapter)
    assert isinstance(hosted_adapter, HostedMcpToolAdapter)
    assert isinstance(stdio_adapter, UnsupportedMcpToolAdapter)


def _local_test_egress_policy() -> EgressUrlPolicy:
    return EgressUrlPolicy(
        allowed_schemes=frozenset({"http", "https"}),
        allow_private_addresses=True,
    )


class _FakeCallToolResult:
    def __init__(
        self,
        *,
        content: list[_FakeContent] | None = None,
        structured_content: dict[str, object] | None = None,
        is_error: bool = False,
    ) -> None:
        self.content = content or []
        self.structuredContent = structured_content
        self.isError = is_error


class _FakeContent:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def model_dump(self, **_: object) -> dict[str, object]:
        return self._payload


class _FakeStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("remote secret must not escape")
        self.response = _FakeStatusResponse(status_code)


class _FakeStatusResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeTransport:
    def __init__(self, streams: tuple[object, ...]) -> None:
        self._streams = streams

    async def __aenter__(self) -> tuple[object, ...]:
        return self._streams

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FakeClientSession:
    def __init__(self, sdk: _FakeMcpSdk) -> None:
        self._sdk = sdk

    async def __aenter__(self) -> _FakeClientSession:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def initialize(self) -> None:
        self._sdk.initialize_calls += 1

    async def call_tool(
        self,
        name: str,
        *,
        arguments: dict[str, object],
        read_timeout_seconds: object,
    ) -> _FakeCallToolResult:
        self._sdk.tool_calls.append(
            {
                "name": name,
                "arguments": arguments,
                "timeout_seconds": read_timeout_seconds.total_seconds(),
            }
        )
        outcome = self._sdk.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeMcpSdk:
    def __init__(self, outcomes: list[_FakeCallToolResult | Exception]) -> None:
        self.outcomes = outcomes
        self.transport_calls: list[dict[str, Any]] = []
        self.initialize_calls = 0
        self.tool_calls: list[dict[str, Any]] = []

    def streamable_http_client(
        self,
        url: str,
        *,
        http_client: Any,
    ) -> _FakeTransport:
        headers = {
            key: value
            for key, value in http_client.headers.items()
            if key in {"authorization", "x-static", "x-tenant", "x-api-key"}
        }
        self.transport_calls.append(
            {
                "transport": "streamable_http",
                "url": url,
                "headers": headers,
                "timeout": http_client.timeout.connect,
            }
        )
        return _FakeTransport((object(), object(), None))

    def client_session(self, read_stream: object, write_stream: object) -> _FakeClientSession:
        return _FakeClientSession(self)


class RecordingRuntimeManager:
    def __init__(self, result: RuntimeCommandResult) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    def execute_command(
        self,
        *,
        workspace_id: object,
        runtime: WorkspaceRuntime,
        command: list[str],
    ) -> RuntimeCommand:
        self.calls.append(
            {
                "workspace_id": workspace_id,
                "runtime": runtime,
                "command": command,
            }
        )
        return RuntimeCommand(
            workspace_id=workspace_id,
            workspace_runtime_id=runtime.id,
            runtime_space_id=runtime.runtime_space_id,
            command=command,
            status="completed" if self._result.exit_code == 0 else "failed",
            exit_code=self._result.exit_code,
            stdout=self._result.stdout,
            stderr=self._result.stderr,
        )
