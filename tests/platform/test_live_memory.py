"""A run-bound live memory tool authorizes writes and releases stale callbacks."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.memory.api import MemoryNamespace, MemoryPolicy
from opsmesh.modules.runtime.api import AgentRunView


async def test_live_memory_binding_uses_revision_and_run_identity(tmp_path: Path) -> None:
    app = OpsMeshApplication(OpsMeshSettings(data_directory=tmp_path, worker_enabled=False))
    await app.initialize(admin_username="admin", admin_password="local-testing-password")
    try:
        principal = (await app.services.accounts.list_users())[0]
        await app.services.memory.create_namespace(
            MemoryNamespace(id="memory", owner=principal.id, policy=MemoryPolicy(live_update=True))
        )
        now = datetime.now(UTC)
        run = AgentRunView(
            id="run",
            revision=1,
            principal_id=principal.id,
            session_id="s",
            agent=AgentSpecification(id="a", name="agent"),
            status="running",
            attempt_id="attempt1",
            created_at=now,
            updated_at=now,
        )
        prepared = await app.services.memory_tools.prepare(run, "memory")
        tool = prepared.tools[0]
        assert tool.tool_ref is not None
        result = await app.services.tool_invoker.invoke(
            tool.tool_ref,
            {
                "summary": "new summary",
                "index": "new index",
                "expected_revision": 1,
                "idempotency_key": "write1",
            },
            principal_id=principal.id,
            call_id="live-1",
        )
        assert result["memory_revision"] == 1
        assert (
            await app.services.memory.read("memory", principal_id=principal.id)
        ).summary == "new summary"
        current = await app.services.tool_invoker.invoke(
            prepared.tools[1].tool_ref, {}, principal_id=principal.id, call_id="read-live"
        )
        assert current["revision"] == 2 and current["summary"] == "new summary"
        catalog = await app.services.capabilities.get_tool(tool.tool_ref)
        callback = app.services.code_tools.resolve(catalog.code_ref)
        app.services.memory_tools.release(run)
        with pytest.raises(OpsMeshError, match="released"):
            await callback({})
    finally:
        await app.close()
