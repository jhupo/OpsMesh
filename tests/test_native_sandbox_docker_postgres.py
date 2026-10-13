"""Real SDK Docker sessions with product ownership and PostgreSQL receipts."""

import asyncio
import io
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from agents.sandbox import Manifest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.capabilities.mcp.sandbox_stdio import SandboxMCPServerStdio
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.sandboxes.factory import build_sandbox_backends
from opsmesh.runtime.sandboxes.models import SandboxReference
from opsmesh.runtime.sandboxes.service import SandboxService
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.shared.config import Settings
from opsmesh.shared.errors import NotFoundError, PolicyDeniedError
from tests.fixtures.agent_product import _seed_workspace, _session

pytestmark = [
    pytest.mark.native_sandbox,
    pytest.mark.skipif(
        sys.platform != "linux" or os.getenv("OPSMESH_TEST_NATIVE_SANDBOX") != "1",
        reason="Set OPSMESH_TEST_NATIVE_SANDBOX=1 on a Linux Docker worker",
    ),
]


def test_native_docker_recovery_scope_and_managed_mcp(tmp_path: Path) -> None:
    image = os.environ["OPSMESH_TEST_COMPUTE_IMAGE"]
    session = _session()
    _, workspace = _seed_workspace(session, with_default_provider=False)
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Native Docker acceptance",
        sandbox_backend_id="docker",
        sandbox_options={"image": image},
    )
    session.add(runtime)
    session.commit()
    settings = Settings(environment="test", storage_root=str(tmp_path / "storage"))
    services = ActivityDependencies(
        sessionmaker(session.get_bind(), expire_on_commit=False), settings
    )
    registry = build_sandbox_backends(settings)
    service = SandboxService(services.transaction, registry, services.secrets())

    async def exercise() -> None:
        references = []
        try:
            for _ in range(2):
                references.append(
                    await service.create(
                        workspace_id=workspace.id,
                        runtime_id=runtime.id,
                        owner_kind="run",
                        owner_id=uuid4(),
                        manifest=Manifest(root="/workspace"),
                    )
                )
            first, second = references
            assert first.native_session_id != second.native_session_id
            first_session = await service.resume(workspace.id, first.id)
            await first_session.write(
                Path(first_session.state.manifest.root) / "receipt.txt", io.BytesIO(b"persisted")
            )
            with pytest.raises(NotFoundError):
                await service.resume(uuid4(), first.id)
            with pytest.raises(PolicyDeniedError):
                await service.read_file(workspace.id, first.id, "../escape", max_bytes=100)
            await service.stop(workspace.id, first.id)
            assert (
                await service.read_file(workspace.id, first.id, "receipt.txt", max_bytes=100)
                == b"persisted"
            )
            session.expire_all()
            durable = session.get(SandboxReference, first.id)
            assert durable is not None and durable.encrypted_session_state
            assert first.native_session_id not in durable.encrypted_session_state
            await service.delete(workspace.id, first.id)
            second_session = await service.resume(workspace.id, second.id)
            await second_session.write(
                Path(second_session.state.manifest.root) / "alive.txt",
                io.BytesIO(b"other execution"),
            )
            assert (
                await service.read_file(workspace.id, second.id, "alive.txt", max_bytes=100)
                == b"other execution"
            )

            client = SandboxMCPServerStdio(
                session_factory=services.transaction,
                backends=registry,
                secrets=services.secrets(),
                workspace_id=workspace.id,
                runtime_id=runtime.id,
                server_id=uuid4(),
                command=["python", "-c", Path("tests/fixtures/mcp_stdio_server.py").read_text()],
                environment={"MCP_PRIVATE_FIXTURE": "native-private-canary"},
                cwd="/workspace",
                name="Native product MCP acceptance",
                timeout_seconds=30,
            )
            async with client:
                result = await client.call_tool("echo", {"message": "product-native-stdio"})
                assert not result.is_error
                assert result.structured_content == {"message": "product-native-stdio"}
            session.expire_all()
            mcp_reference = session.scalar(
                select(SandboxReference).where(
                    SandboxReference.workspace_id == workspace.id,
                    SandboxReference.owner_kind == "mcp",
                )
            )
            assert mcp_reference is not None and mcp_reference.status == "deleted"
            assert mcp_reference.encrypted_session_state is None
            assert not client.params.args and client.params.env is None
        finally:
            for reference in references:
                await service.delete(workspace.id, reference.id)

    try:
        asyncio.run(exercise())
    finally:
        registry.close()
