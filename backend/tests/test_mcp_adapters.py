from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4

from backend.app.capabilities.adapters import (
    DockerRuntimeStdioMcpToolAdapter,
    HostedMcpToolAdapter,
    HttpJsonRpcMcpToolAdapter,
    McpAdapterResolver,
    SseMcpToolAdapter,
    UnsupportedMcpToolAdapter,
)
from backend.app.capabilities.execution import McpExecutionError
from backend.app.capabilities.models import McpCredentialReference, McpServer
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.runtime_manager.contracts import RuntimeCommandResult
from backend.app.runtimes.models import RuntimeCommand, WorkspaceRuntime
from backend.app.secrets.service import SecretEncryptionService


def test_http_jsonrpc_mcp_adapter_posts_tool_call_and_injects_hosted_headers() -> None:
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

    with JsonRpcServer({"result": {"content": [{"type": "text", "text": "ok"}]}}) as server:
        response = HttpJsonRpcMcpToolAdapter(secret_service=secret_service).call(
            server=McpServer(
                workspace_id=uuid4(),
                name="http-tools",
                server_type="http",
                connection={
                    "url": server.url,
                    "headers": {"x-static": "yes"},
                },
            ),
            tool_name="generate_image",
            arguments={"prompt": "mountain"},
            credential_refs=[credential],
            timeout_seconds=5,
        )

    assert response == {"content": [{"type": "text", "text": "ok"}]}
    assert server.requests[0]["headers"]["authorization"] == "Bearer secret-token"
    assert server.requests[0]["headers"]["x-tenant"] == "acme"
    assert server.requests[0]["headers"]["x-static"] == "yes"
    assert server.requests[0]["body"]["method"] == "tools/call"
    assert server.requests[0]["body"]["params"] == {
        "name": "generate_image",
        "arguments": {"prompt": "mountain"},
    }


def test_http_jsonrpc_mcp_adapter_sanitizes_remote_errors() -> None:
    with JsonRpcServer(
        {
            "error": {
                "code": -32000,
                "message": "remote leaked secret-token",
            }
        }
    ) as server:
        try:
            HttpJsonRpcMcpToolAdapter().call(
                server=McpServer(
                    workspace_id=uuid4(),
                    name="http-tools",
                    server_type="http",
                    connection={"url": server.url},
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


def test_hosted_mcp_adapter_delegates_to_remote_http_transport() -> None:
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

    with JsonRpcServer({"result": {"ok": True}}) as server:
        response = HostedMcpToolAdapter(secret_service=secret_service).call(
            server=McpServer(
                workspace_id=uuid4(),
                name="hosted-tools",
                server_type="hosted",
                connection={"transport": "http_jsonrpc", "url": server.url},
            ),
            tool_name="generate_image",
            arguments={"prompt": "mountain"},
            credential_refs=[credential],
            timeout_seconds=5,
        )

    assert response == {"ok": True}
    assert server.requests[0]["headers"]["x-api-key"] == "secret-key"


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
            server_type="http",
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
            connection={"transport": "http_jsonrpc", "url": "https://example.test/mcp"},
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

    assert isinstance(http_adapter, HttpJsonRpcMcpToolAdapter)
    assert isinstance(sse_adapter, SseMcpToolAdapter)
    assert isinstance(hosted_adapter, HostedMcpToolAdapter)
    assert isinstance(stdio_adapter, UnsupportedMcpToolAdapter)


class JsonRpcServer:
    def __init__(self, response_body: dict[str, object]) -> None:
        self._response_body = response_body
        self.requests: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("Server is not running")
        host, port = self._server.server_address
        return f"http://{host}:{port}/mcp"

    def __enter__(self) -> JsonRpcServer:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                content_length = int(self.headers.get("content-length", "0"))
                raw_body = self.rfile.read(content_length)
                parent.requests.append(
                    {
                        "headers": {key.lower(): value for key, value in self.headers.items()},
                        "body": json.loads(raw_body.decode("utf-8")),
                    }
                )
                response = {
                    "jsonrpc": "2.0",
                    "id": parent.requests[-1]["body"].get("id"),
                    **parent._response_body,
                }
                response_bytes = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(response_bytes)))
                self.end_headers()
                self.wfile.write(response_bytes)

            def log_message(self, format: str, *args: object) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


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
