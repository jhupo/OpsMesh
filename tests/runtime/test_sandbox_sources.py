"""Native state and workspace snapshot select different SDK client operations."""

import os
import sys
from datetime import UTC, datetime

import pytest

from opsmesh.application.sandbox_bridge import SandboxWorkspaceBridge
from opsmesh.framework.agents import AgentExecutionContext, AgentSpecification, SandboxConfiguration
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_lifecycle import (
    ManagedSandboxSession,
)
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32" or os.getenv("OPSMESH_TEST_SANDBOX_INTEGRATION") != "1",
    reason="Explicit Unix native sandbox integration",
)
@pytest.mark.asyncio
async def test_native_session_resume_and_workspace_snapshot_have_distinct_identity(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'sources.db'}")
    workspaces = LocalWorkspaceService(database, tmp_path / "data")
    bridge = SandboxWorkspaceBridge(
        workspaces,
        database,
        allow_trusted_local=True,
        session_factory=ManagedSandboxSession,
    )
    await database.initialize()
    authorized = []

    async def invoke(*args):
        raise AssertionError("No model calls")

    async def authorize(workspace_id):
        authorized.append(workspace_id)

    async def prepare(run_id, *, principal="alice", **source):
        specification = AgentSpecification(
            id="source",
            name="Source",
            sandbox=SandboxConfiguration(provider="local", trusted_local=True, **source),
        )
        view = AgentRunView(
            id=run_id,
            revision=1,
            principal_id=principal,
            session_id="session",
            agent=specification,
            status="running",
            attempt_id=run_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        context = AgentExecutionContext(
            run_id, "session", principal, invoke, services={"authorize_workspace": authorize}
        )
        context.services.update(await bridge.prepare(view, context))
        return context

    try:
        first = await prepare("original")
        session = first.services["openai_sandbox_session"]
        identity = session.state.session_id
        await session._exec_checked_nonzero("sh", "-c", "printf durable > artifact.txt")
        source = await first.services["checkpoint_resources"]()
        resumed = await prepare("resumed", session_ref="original")
        resumed_session = resumed.services["openai_sandbox_session"]
        assert resumed_session.state.session_id == identity
        assert await resumed_session.read_bounded("artifact.txt", max_bytes=100) == b"durable"
        await resumed.services["checkpoint_resources"]()
        copied = await prepare("snapshot", snapshot_ref=source["workspace_snapshot_id"])
        assert copied.services["openai_sandbox_session"].state.session_id != identity
        assert (
            await copied.services["openai_sandbox_session"].read_bounded(
                "artifact.txt", max_bytes=100
            )
            == b"durable"
        )
        assert source["workspace_id"] in authorized
        await copied.services["checkpoint_resources"]()
        with pytest.raises(OpsMeshError) as forbidden:
            await prepare("foreign", principal="mallory", session_ref="original")
        assert forbidden.value.code == "AUTHORIZATION_DENIED"
    finally:
        await bridge.close()
        await database.close()
