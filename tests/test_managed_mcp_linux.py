"""Managed stdio uses the SDK native Docker session and SDK protocol client."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agents.mcp import MCPServerStdio
from mcp.client.stdio import StdioServerParameters

from opsmesh.capabilities.mcp.sandbox_stdio import SandboxMCPServerStdio
from opsmesh.runtime.sandboxes.service import SandboxReferenceSnapshot
from opsmesh.shared.security.secrets import SecretEncryptionService
from tests.fixtures.native_sandbox import native_boundary


@pytest.mark.parametrize("failure", [False, True])
def test_trusted_duplex_transport_uses_only_native_container_and_cleanup_scope(
    monkeypatch, failure
):
    native, _, registry = native_boundary()
    workspace_id, runtime_id, server_id = uuid4(), uuid4(), uuid4()
    reference = SandboxReferenceSnapshot(
        uuid4(), runtime_id, "docker", str(native.state.session_id), "running", None, None
    )
    sandbox = AsyncMock()
    sandbox.create.return_value = reference
    sandbox.resume.return_value = native
    monkeypatch.setattr(
        "opsmesh.capabilities.mcp.sandbox_stdio.SandboxService", lambda *args: sandbox
    )
    executable = str(Path("docker.exe").resolve())
    monkeypatch.setattr(
        "opsmesh.capabilities.mcp.sandbox_stdio.shutil.which", lambda name: executable
    )
    connect = AsyncMock(side_effect=RuntimeError("handshake failed") if failure else None)
    cleanup = AsyncMock()
    monkeypatch.setattr(MCPServerStdio, "connect", connect)
    monkeypatch.setattr(MCPServerStdio, "cleanup", cleanup)
    client = SandboxMCPServerStdio(
        session_factory=lambda: None,
        backends=registry,
        secrets=SecretEncryptionService(secret="test-key", key_id="test"),
        workspace_id=workspace_id,
        runtime_id=runtime_id,
        server_id=server_id,
        command=["python", "/app/server.py"],
        environment={"BUSINESS_KEY": "private-canary"},
        cwd="/workspace",
        name="Managed",
        timeout_seconds=5,
    )

    async def exercise():
        if failure:
            with pytest.raises(RuntimeError):
                await client.connect()
        else:
            await client.connect()
            assert client.params.args == [
                "exec",
                "-i",
                "--workdir",
                "/workspace",
                "--env",
                "BUSINESS_KEY",
                "native-fixture",
                "python",
                "/app/server.py",
            ]
            assert "private-canary" not in str(client.params.args)
            assert client.params.env["BUSINESS_KEY"] == "private-canary"
            await client.cleanup()
        sandbox.delete.assert_awaited_once_with(workspace_id, reference.id)
        assert not client._environment
        assert client.params.env is None
        assert not client.params.args

    asyncio.run(exercise())


def test_native_deletion_failure_does_not_retain_decrypted_credentials(monkeypatch):
    _, _, registry = native_boundary()
    sandbox = AsyncMock()
    sandbox.delete.side_effect = RuntimeError("native deletion uncertain")
    monkeypatch.setattr(
        "opsmesh.capabilities.mcp.sandbox_stdio.SandboxService", lambda *args: sandbox
    )
    monkeypatch.setattr(
        "opsmesh.capabilities.mcp.sandbox_stdio.shutil.which",
        lambda name: str(Path("docker.exe").resolve()) if name == "docker" else name,
    )
    monkeypatch.setattr(MCPServerStdio, "cleanup", AsyncMock())
    client = SandboxMCPServerStdio(
        session_factory=lambda: None,
        backends=registry,
        secrets=SecretEncryptionService(secret="test-key", key_id="test"),
        workspace_id=uuid4(),
        runtime_id=uuid4(),
        server_id=uuid4(),
        command=["python", "/app/server.py"],
        environment={"BUSINESS_KEY": "private-canary"},
        cwd="/workspace",
        name="Managed",
        timeout_seconds=5,
    )
    client._reference_id = uuid4()
    client.params = client.params.model_copy(update={"env": dict(client._environment)})
    with pytest.raises(RuntimeError, match="native deletion uncertain"):
        asyncio.run(client.cleanup())
    assert not client._environment
    assert client.params.env is None
    assert client._reference_id is not None


def test_public_sdk_stream_extension_does_not_write_private_stderr_to_worker_logs(
    monkeypatch, capfd
):
    _, _, registry = native_boundary()
    monkeypatch.setattr(
        "opsmesh.capabilities.mcp.sandbox_stdio.shutil.which",
        lambda name: str(Path("docker.exe").resolve()) if name == "docker" else name,
    )
    client = SandboxMCPServerStdio(
        session_factory=lambda: None,
        backends=registry,
        secrets=SecretEncryptionService(secret="test-key", key_id="test"),
        workspace_id=uuid4(),
        runtime_id=uuid4(),
        server_id=uuid4(),
        command=["python", "/app/server.py"],
        environment={},
        cwd="/workspace",
        name="Trusted test protocol fixture",
        timeout_seconds=5,
    )
    # Only this trusted test fixture executes on the test process host; production
    # connect() always sets the native sandbox's Docker exec transport.
    client.params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path("tests/fixtures/mcp_stdio_server.py").resolve())],
        env={"MCP_PRIVATE_FIXTURE": "private-stderr-canary"},
    )

    async def exercise():
        await MCPServerStdio.connect(client)
        try:
            result = await client.call_tool("emit_private_stderr", {})
            assert result.structured_content == {"emitted": True}
        finally:
            await client.cleanup()

    asyncio.run(exercise())
    assert "private-stderr-canary" not in "".join(capfd.readouterr())
