"""Frozen identity and SDK Runtime transfer limits are enforced at the process boundary."""

import asyncio
import io
from dataclasses import replace
from pathlib import PurePosixPath
from uuid import uuid4

import pytest

from opsmesh.agents.execution.contracts import (
    AgentRunRequest,
    AgentRuntimeContext,
    AgentRuntimeProfile,
    AgentRuntimeToolDefinition,
    AgentRuntimeToolResult,
)
from opsmesh.agents.execution.providers.openai.mcp import RuntimeMCPServer
from opsmesh.runtime.agent_host.broker import RuntimeCallbackBroker
from opsmesh.runtime.agent_host.local_sandbox import LocalSandboxExecutor
from opsmesh.runtime.agent_host.wire import CONTEXT, RuntimeRunInput
from opsmesh.runtime.backends.docker import DockerSandboxSessionExecutor
from opsmesh.runtime.contracts import SandboxBinding, SandboxManifest, SandboxSession


def request():
    workspace, run = uuid4(), uuid4()
    return AgentRunRequest(
        AgentRuntimeProfile(uuid4(), workspace, 1, "Reviewer", "expert", "Review.", "test"),
        "Review the result.",
        AgentRuntimeContext(workspace, None, run),
    )


def test_callback_rejects_tenant_and_grant_mutation_before_calling_tools():
    class Tools:
        async def execute_tool(self, **kwargs):
            pytest.fail("An altered context must not reach tool execution")

    original = request()
    broker = RuntimeCallbackBroker(replace(original, tool_executor=Tools()))
    for altered in (
        replace(original.context, workspace_id=uuid4()),
        replace(original.context, run_id=uuid4()),
        replace(original.context, allowed_tools=("unauthorized",)),
    ):
        with pytest.raises(ValueError, match="authorized tool context"):
            asyncio.run(
                broker.dispatch(
                    "tool.execute",
                    {
                        "context": CONTEXT.dump_python(altered, mode="json"),
                        "tool_name": "unauthorized",
                        "arguments": {},
                        "approval_granted": True,
                    },
                )
            )


def test_wire_carries_frozen_limits_without_live_database_or_docker_objects():
    original = request()
    executor = DockerSandboxSessionExecutor(object(), "container", "/workspace", 12, 128)
    binding = SandboxBinding(
        SandboxManifest(original.context.run_id, "/workspace"),
        SandboxSession("run", "/workspace", "docker", executor),
    )
    wire = RuntimeRunInput.from_request(replace(original, sandbox=binding))
    payload = wire.model_dump_json()
    assert wire.sandbox_timeout_seconds == 12
    assert wire.sandbox_max_file_bytes == 128
    assert "database_url" not in payload and "container" not in payload


def test_native_mcp_error_content_reaches_the_sdk_while_audit_status_is_failed():
    payload = {"content": [{"type": "text", "text": "Order unavailable"}], "isError": True}

    class Tools:
        async def execute_tool(self, **kwargs):
            return AgentRuntimeToolResult(status="failed", output=payload)

    original = request()
    definition = AgentRuntimeToolDefinition("query", "mcp", "Query", {"type": "object"})
    context = replace(original.context, tool_definitions=(definition,))
    server = RuntimeMCPServer(replace(original, context=context, tool_executor=Tools()))
    result = asyncio.run(server.call_tool("query", {}, {"opsmesh_tool_call_id": "call"}))
    assert result.isError is True
    assert result.content[0].text == "Order unavailable"


def test_local_runtime_files_reject_oversized_and_outside_workspace_access(tmp_path):
    executor = LocalSandboxExecutor(str(tmp_path), 1, 8)
    target = PurePosixPath((tmp_path / "bounded.txt").as_posix())
    executor.write_file(target, io.BytesIO(b"allowed"))
    assert executor.read_file(target) == b"allowed"
    with pytest.raises(ValueError, match="transfer limit"):
        executor.write_file(target, io.BytesIO(b"too large"))
    assert executor.read_file(target) == b"allowed"
    (tmp_path / "oversized").write_bytes(b"a" * 9)
    with pytest.raises(ValueError, match="transfer limit"):
        executor.read_file(PurePosixPath((tmp_path / "oversized").as_posix()))
    with pytest.raises(ValueError, match="outside"):
        executor.read_file(PurePosixPath((tmp_path.parent / "foreign.txt").as_posix()))
