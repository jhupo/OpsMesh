import asyncio
from uuid import uuid4

import pytest

from opsmesh.capabilities.mcp.execution.contracts import McpExecutionError
from opsmesh.capabilities.mcp.models import McpCredentialReference, McpServer
from opsmesh.capabilities.mcp.transport.payloads import stdio_command
from opsmesh.capabilities.mcp.transport.runtime_http import DockerRuntimeHttpMcpToolAdapter
from opsmesh.capabilities.mcp.transport.stdio import DockerRuntimeStdioMcpToolAdapter
from opsmesh.capabilities.mcp.transport.stdio_credentials import (
    self_hosted_stdio_environment_refs,
)
from opsmesh.runtime.agent_host.wire import RpcFrame
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.shared.concurrency import BlockingIO
from opsmesh.shared.security.secrets import SecretEncryptionService


class RuntimeChannel:
    def __init__(self, block=False):
        self.request = None
        self.closed = False
        self.block = block
        self.entered = asyncio.Event()
        self.frames = 0

    async def attach(self):
        pass

    async def send(self, data):
        import json

        self.request = json.loads(data)

    async def receive(self):
        self.frames += 1
        if self.frames == 1:
            return RpcFrame(
                type="ready", payload={"pid": 27, "operation_id": self.request["operation_id"]}
            ).encoded()
        self.entered.set()
        if self.block:
            await asyncio.Event().wait()
        return RpcFrame(
            type="result",
            payload={"content": [], "structuredContent": {"ok": True}, "isError": False},
        ).encoded()

    async def close(self):
        self.closed = True


class RuntimeDocker:
    def __init__(self, channel):
        self.channel = channel
        self.terminated = []

    def open_mcp_channel(self, container_id, *, working_dir):
        return self.channel

    def terminate_agent_process(self, container_id, pid):
        self.terminated.append((container_id, pid))


def runtime():
    return WorkspaceRuntime(
        id=uuid4(), workspace_id=uuid4(), docker_container_id="runtime-test", status="running"
    )


def test_stdio_request_executes_in_runtime_and_credentials_stay_in_private_payload():
    target = runtime()
    channel = RuntimeChannel()
    docker = RuntimeDocker(channel)
    secrets = SecretEncryptionService(secret="test-secret", key_id="test")
    encrypted = secrets.encrypt_payload({"env": {"MCP_KEY": "private-token"}})
    credential = McpCredentialReference(
        provider="hosted", encrypted_secret_payload=encrypted.ciphertext
    )
    operation = DockerRuntimeStdioMcpToolAdapter(
        docker=docker, runtime=target, secret_service=secrets
    ).prepare(
        server=McpServer(
            workspace_id=target.workspace_id,
            connection={"command": "mcp-test", "args": ["--stdio"]},
        ),
        tool_name="lookup",
        arguments={},
        credential_refs=[credential],
        timeout_seconds=5,
    )

    async def execute():
        with BlockingIO(1, name="mcp-test") as io:
            return await operation.execute(io)

    result = asyncio.run(execute())
    assert result["structuredContent"] == {"ok": True}
    assert channel.request["request"]["server"]["env"] == {"MCP_KEY": "private-token"}
    assert "private-token" not in repr(operation)
    assert channel.closed and docker.terminated == [("runtime-test", 27)]


def test_cancelled_operation_terminates_runtime_process_and_closes_socket():
    target = runtime()

    async def execute():
        channel = RuntimeChannel(block=True)
        docker = RuntimeDocker(channel)
        operation = DockerRuntimeStdioMcpToolAdapter(docker=docker, runtime=target).prepare(
            server=McpServer(
                workspace_id=target.workspace_id, connection={"command": "mcp-test", "args": []}
            ),
            tool_name="lookup",
            arguments={},
            credential_refs=[],
            timeout_seconds=5,
        )
        with BlockingIO(1, name="mcp-test") as io:
            task = asyncio.create_task(operation.execute(io))
            await channel.entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert channel.closed and docker.terminated == [("runtime-test", 27)]

    asyncio.run(execute())


def test_remote_private_network_is_rejected_before_runtime_execution():
    target = runtime()
    docker = RuntimeDocker(RuntimeChannel())
    with pytest.raises(McpExecutionError):
        DockerRuntimeHttpMcpToolAdapter(docker, target, None).prepare(
            server=McpServer(
                workspace_id=target.workspace_id,
                server_type="sse",
                connection={"url": "http://127.0.0.1/mcp"},
            ),
            tool_name="lookup",
            arguments={},
            credential_refs=[],
            timeout_seconds=5,
        )


@pytest.mark.parametrize(
    "connection", [{"command": ["mcp-test"]}, {"command": "mcp-test", "args": "--stdio"}]
)
def test_stdio_rejects_noncanonical_command(connection):
    with pytest.raises(McpExecutionError):
        stdio_command(connection)


def test_self_hosted_stdio_credentials_block_connector_runtime_credential() -> None:
    credential = McpCredentialReference(
        workspace_id=uuid4(),
        name="forbidden",
        provider="self_hosted_env",
        external_ref="env:OPSMESH_RUNTIME_CREDENTIAL",
    )

    try:
        self_hosted_stdio_environment_refs([credential])
    except McpExecutionError as exc:
        assert exc.code == "mcp_self_hosted_credential_forbidden"
        assert "OPSMESH_RUNTIME_CREDENTIAL" not in str(exc)
    else:
        raise AssertionError("Expected the connector credential to remain unavailable to MCP")
