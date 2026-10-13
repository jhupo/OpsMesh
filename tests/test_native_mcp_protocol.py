"""Offline real MCP 2 protocol and credential rejection at native SDK boundaries."""

import asyncio
import json
import socket
from collections.abc import Iterator
from threading import Thread
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest
import uvicorn
from agents.mcp import MCPServerStreamableHttp
from mcp.server import MCPServer

from opsmesh.capabilities.mcp.execution.diagnostics import native_failure, normalize_failure
from opsmesh.capabilities.mcp.models import MCPCredentialReference
from opsmesh.capabilities.mcp.sandbox_stdio import _validate_cli_environment
from opsmesh.capabilities.mcp.transport.credentials import resolve_hosted_credentials
from opsmesh.capabilities.mcp.transport.remote import list_remote_mcp_tools_async
from opsmesh.shared.errors import PolicyDeniedError
from opsmesh.shared.security.secrets import SecretEncryptionService


@pytest.fixture
def native_mcp_url() -> Iterator[str]:
    protocol = MCPServer("Native protocol fixture")

    @protocol.tool()
    def echo(message: str) -> dict[str, str]:
        return {"message": message}

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            protocol.streamable_http_app(stateless_http=True, json_response=True),
            log_level="critical",
        )
    )
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 5
        while not server.started:
            assert thread.is_alive() and monotonic() < deadline, "MCP fixture did not start"
            sleep(0.01)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive(), "MCP fixture failed to release its server"


def test_native_discovery_and_sdk_tool_result_use_current_mcp_protocol(native_mcp_url: str) -> None:
    async def exercise() -> None:
        tools = await list_remote_mcp_tools_async(
            url=native_mcp_url, headers={}, timeout_seconds=5, transport="http"
        )
        assert [tool["name"] for tool in tools] == ["echo"]
        assert tools[0]["inputSchema"]["properties"]["message"]["type"] == "string"
        async with MCPServerStreamableHttp(
            params={"url": native_mcp_url}, failure_error_function=None
        ) as server:
            result = await server.call_tool("echo", {"message": "native protocol"})
        assert result.structured_content == {"message": "native protocol"}
        assert not result.is_error

    asyncio.run(exercise())


def test_native_mcp_credentials_use_rotation_key_and_reject_conflicting_headers() -> None:
    old = SecretEncryptionService(secret="old-test-key", key_id="old")
    encrypted = old.encrypt_payload({"bearer_token": "private-rotation-canary"})
    credential = MCPCredentialReference(
        id=uuid4(),
        workspace_id=uuid4(),
        provider="hosted",
        name="Rotated",
        encrypted_secret_payload=encrypted.ciphertext,
        encryption_key_id=encrypted.key_id,
    )
    current = SecretEncryptionService(
        secret="new-test-key", key_id="current", previous_secrets={"old": "old-test-key"}
    )
    environment, headers = resolve_hosted_credentials([credential], current)
    assert not environment
    assert headers == {"authorization": "Bearer private-rotation-canary"}
    assert "private-rotation-canary" not in json.dumps(credential.encrypted_secret_payload)

    conflicting = current.encrypt_payload({"headers": {"Authorization": "conflict"}})
    other = MCPCredentialReference(
        id=uuid4(),
        workspace_id=credential.workspace_id,
        provider="hosted",
        name="Other",
        encrypted_secret_payload=conflicting.ciphertext,
        encryption_key_id=conflicting.key_id,
    )
    with pytest.raises(PolicyDeniedError, match="duplicate"):
        resolve_hosted_credentials([credential, other], current)


@pytest.mark.parametrize("name", ["DOCKER_HOST", "PATH", "LD_PRELOAD", "PYTHONPATH", "9KEY"])
def test_mcp_user_credentials_cannot_control_trusted_transport_process(name: str) -> None:
    with pytest.raises(PolicyDeniedError, match="reserved"):
        _validate_cli_environment({name: "private-canary"})


def test_native_mcp_exception_status_is_preserved_without_vendor_secret_text() -> None:
    request = httpx.Request("POST", "https://example.test/tool?api_key=private-canary")
    failure = httpx.HTTPStatusError(
        "password=private-canary", request=request, response=httpx.Response(503, request=request)
    )
    result = native_failure(ExceptionGroup("private-canary", [failure]))
    assert result["http_status"] == 503
    assert result["exception_type"] == "HTTPStatusError"
    assert result["retryable"] is True
    assert "private-canary" not in json.dumps(result)
    result = normalize_failure(
        {"http_status": 401, "exception_type": "AuthenticationError", "password": "private-canary"},
        code="mcp_tool_error",
        message="MCP tool returned an error",
    )
    assert result["http_status"] == 401
    assert "private-canary" not in json.dumps(result)
