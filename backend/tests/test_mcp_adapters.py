from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4

from backend.app.capabilities.adapters import (
    HttpJsonRpcMcpToolAdapter,
    McpAdapterResolver,
    UnsupportedMcpToolAdapter,
)
from backend.app.capabilities.execution import McpExecutionError
from backend.app.capabilities.models import McpCredentialReference, McpServer
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


def test_mcp_adapter_resolver_selects_http_and_blocks_unsafe_direct_stdio() -> None:
    resolver = McpAdapterResolver()

    http_adapter = resolver.resolve(
        McpServer(
            workspace_id=uuid4(),
            name="http-tools",
            server_type="http",
            connection={"url": "https://example.test/mcp"},
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
