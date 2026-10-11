import asyncio
import socket
from threading import Thread
from time import monotonic, sleep
from uuid import uuid4

import uvicorn
from mcp.server.fastmcp import FastMCP

from opsmesh.capabilities.mcp.models import McpCredentialReference, McpServer
from opsmesh.capabilities.mcp.transport.runtime_http import DockerRuntimeHttpMcpToolAdapter
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.shared.concurrency import BlockingIO
from opsmesh.shared.security.secrets import SecretEncryptionService
from runtime.opsmesh_runtime.mcp_http_client import execute_request
from tests.fixtures.runtime_host import execution_identity, runtime_host
from tests.test_mcp_adapters import RuntimeChannel, RuntimeDocker


def test_runtime_http_calls_real_native_sdk_server():
    mcp = FastMCP("Offline Runtime HTTP", stateless_http=True, json_response=True)

    @mcp.tool()
    def echo(message: str) -> dict[str, str]:
        return {"message": message}

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), log_level="critical"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 5
        while not server.started:
            assert monotonic() < deadline, "Local MCP fixture did not start"
            sleep(0.01)
        result = asyncio.run(
            execute_request(
                {
                    "contract_version": 2,
                    "transport": "http",
                    "server": {"url": f"http://127.0.0.1:{port}/mcp", "timeout": 5},
                    "tool": {
                        "name": "echo",
                        "arguments": {"message": "native-http"},
                        "timeout_seconds": 5,
                    },
                }
            )
        )
        assert result["structuredContent"] == {"message": "native-http"}
        assert result["isError"] is False
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


def test_remote_mcp_runs_in_runtime_with_transient_credentials_and_native_result():
    workspace_id = uuid4()
    secrets = SecretEncryptionService(secret="runtime-http-test", key_id="test")
    encrypted = secrets.encrypt_payload({"bearer_token": "runtime-http-secret"})
    credential = McpCredentialReference(
        workspace_id=workspace_id,
        name="HTTP",
        provider="hosted",
        external_ref="",
        encrypted_secret_payload=encrypted.ciphertext,
        secret_fingerprint=encrypted.fingerprint,
        encryption_key_id=encrypted.key_id,
    )
    runtime = WorkspaceRuntime(
        id=uuid4(),
        workspace_id=workspace_id,
        name="Runtime",
        host=runtime_host(workspace_id, "runtime-http", capacity=16, node_id="test-node"),
        limits={},
    )
    payload = {
        "content": [{"type": "image", "data": "eA==", "mimeType": "image/png"}],
        "structuredContent": {"evidence": "preserved"},
        "isError": False,
    }
    channel = RuntimeChannel()
    original_receive = channel.receive

    async def receive():
        if channel.frames == 1:
            channel.frames += 1
            from opsmesh.runtime.agent_host.wire import RpcFrame

            return RpcFrame(type="result", payload=payload).encoded()
        return await original_receive()

    channel.receive = receive
    docker = RuntimeDocker(channel)
    server = McpServer(
        workspace_id=workspace_id,
        name="Hosted SSE",
        server_type="hosted",
        connection={
            "transport": "sse",
            "url": "https://example.com/mcp",
            "auth_method": "bearer_token",
        },
    )
    operation = DockerRuntimeHttpMcpToolAdapter(
        docker, runtime, secrets, identity=execution_identity()
    ).prepare(
        server=server,
        tool_name="inspect",
        arguments={},
        credential_refs=[credential],
        timeout_seconds=5,
    )

    async def invoke():
        with BlockingIO(1, name="http-mcp-test") as io:
            return await operation.execute(io)

    result = asyncio.run(invoke())
    assert result == payload
    assert channel.closed
    assert "runtime-http-secret" not in repr(operation)
    request = channel.request["request"]
    assert request["transport"] == "sse"
    assert request["server"]["headers"]["authorization"] == "Bearer runtime-http-secret"
