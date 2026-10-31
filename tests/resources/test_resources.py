"""Resource integration tests use local files, processes and injected model callbacks."""

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.capabilities.api import (
    AgentResourceRequest,
    KnowledgeDocument,
    MCPServerDefinition,
    ToolDefinition,
)
from opsmesh.modules.capabilities.internal.catalog import PersistentCapabilityCatalog
from opsmesh.modules.capabilities.internal.code_tools import InProcessCodeToolRegistry
from opsmesh.modules.capabilities.internal.knowledge import LocalKnowledgeIndex
from opsmesh.modules.capabilities.internal.mcp import MCPServerConnector, MCPServerRegistry
from opsmesh.modules.capabilities.internal.resource_compiler import AgentResourceCompiler
from opsmesh.modules.capabilities.internal.skills import PersistentSkillCatalog
from opsmesh.modules.capabilities.internal.tool_invoker import AuthorizedToolInvoker
from opsmesh.modules.execution.api import CommandRequest, ExecutionSpec
from opsmesh.modules.execution.internal.service import (
    DockerExecutionProvider,
    LocalExecutionProvider,
)
from opsmesh.modules.memory.api import (
    MemoryCandidate,
    MemoryExtraction,
    MemoryNamespace,
    MemoryPolicy,
    MemorySource,
)
from opsmesh.modules.memory.internal.service import PersistentMemoryService
from opsmesh.modules.workspaces.api import WorkspaceEntry, WorkspaceManifest
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[AsyncDatabase]:
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.sqlite'}")
    yield database
    await database.close()


async def allow(principal: str, resource: str) -> None:
    if principal != "alice":
        raise OpsMeshError("AUTHORIZATION_DENIED", "No grant")


@pytest.mark.asyncio
async def test_workspace_snapshot_is_immutable_and_restores_to_new_directory(
    database: AsyncDatabase, tmp_path: Path
) -> None:
    service = LocalWorkspaceService(database, tmp_path / "data")
    await database.initialize()
    await service.create("source")
    await service.write("source", "repo/test.txt", b"before")
    await service.materialize("source", WorkspaceManifest(directories=("empty",)))
    snapshot = await service.snapshot("source")
    await service.write("source", "repo/test.txt", b"after")
    await service.restore(snapshot.id, "restored")
    assert await service.read("restored", "repo/test.txt") == b"before"
    assert (await service.root_path("restored") / "empty").is_dir()
    assert await service.read("source", "repo/test.txt") == b"after"
    with pytest.raises(OpsMeshError, match="already exists"):
        await service.restore(snapshot.id, "restored")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["../escape", "/absolute", "C:/file", "a\\b", "a/../b", "a:stream"]
)
async def test_workspace_rejects_unsafe_paths(
    database: AsyncDatabase, tmp_path: Path, path: str
) -> None:
    service = LocalWorkspaceService(database, tmp_path / "data")
    await database.initialize()
    await service.create("workspace")
    with pytest.raises(OpsMeshError):
        await service.write("workspace", path, b"bad")


@pytest.mark.asyncio
async def test_manifest_validation_precedes_writes_and_readonly_is_enforced(
    database: AsyncDatabase, tmp_path: Path
) -> None:
    service = LocalWorkspaceService(database, tmp_path / "data")
    await database.initialize()
    await service.create("workspace")
    with pytest.raises(OpsMeshError):
        await service.materialize(
            "workspace",
            WorkspaceManifest(
                entries=(
                    WorkspaceEntry(path="valid", content="ok"),
                    WorkspaceEntry(path="../bad", content="bad"),
                )
            ),
        )
    with pytest.raises(OpsMeshError):
        await service.read("workspace", "valid")
    await service.write("workspace", "readonly", b"immutable", read_only=True)
    with pytest.raises(OpsMeshError, match="read-only"):
        await service.write("workspace", "readonly", b"changed")
    await service.delete("workspace")


@pytest.mark.asyncio
async def test_blob_integrity_is_checked(database: AsyncDatabase, tmp_path: Path) -> None:
    service = LocalWorkspaceService(database, tmp_path / "data")
    await database.initialize()
    digest = await service.put_blob(b"expected")
    (tmp_path / "data" / "objects" / digest).write_bytes(b"tampered")
    with pytest.raises(OpsMeshError, match="digest mismatch"):
        await service.get_blob(digest)


@pytest.mark.asyncio
async def test_tool_gateway_auth_schema_replay_and_immutable_version(
    database: AsyncDatabase,
) -> None:
    catalog = PersistentCapabilityCatalog(database)
    code = InProcessCodeToolRegistry()
    invoker = AuthorizedToolInvoker(database, catalog, code, allow)
    await database.initialize()
    calls: list[dict[str, Any]] = []

    async def handler(arguments: dict[str, Any]) -> dict[str, int]:
        calls.append(arguments)
        return {"doubled": arguments["value"] * 2}

    code.register("double@1", handler)
    definition = ToolDefinition(
        name="double",
        version="1",
        code_ref="double@1",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    await catalog.register_tool(definition)
    with pytest.raises(OpsMeshError):
        await invoker.invoke("double@1", {"value": 2}, principal_id="bob", call_id="c1")
    with pytest.raises(OpsMeshError):
        await invoker.invoke("double@1", {"value": "2"}, principal_id="alice", call_id="c1")
    assert await invoker.invoke("double@1", {"value": 2}, principal_id="alice", call_id="c1") == {
        "doubled": 4
    }
    assert await invoker.invoke("double@1", {"value": 2}, principal_id="alice", call_id="c1") == {
        "doubled": 4
    }
    assert len(calls) == 1
    with pytest.raises(OpsMeshError):
        await invoker.invoke("double@1", {"value": 3}, principal_id="alice", call_id="c1")
    with pytest.raises(OpsMeshError, match="immutable"):
        await catalog.register_tool(definition.model_copy(update={"description": "changed"}))


@pytest.mark.asyncio
async def test_failed_side_effecting_tool_cannot_be_replayed(database: AsyncDatabase) -> None:
    catalog = PersistentCapabilityCatalog(database)
    code = InProcessCodeToolRegistry()
    invoker = AuthorizedToolInvoker(database, catalog, code, allow)
    await database.initialize()
    calls = 0

    async def handler(arguments: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("after side effect")

    code.register("danger", handler)
    await catalog.register_tool(
        ToolDefinition(
            name="danger",
            version="1",
            input_schema={"type": "object"},
            code_ref="danger",
            side_effects=True,
        )
    )
    for _ in range(2):
        with pytest.raises(OpsMeshError):
            await invoker.invoke("danger@1", {}, principal_id="alice", call_id="same")
    assert calls == 1


@pytest.mark.asyncio
async def test_lazy_skill_assets_are_frozen_and_loaded_on_demand(
    database: AsyncDatabase, tmp_path: Path
) -> None:
    workspace = LocalWorkspaceService(database, tmp_path / "data")
    skills = PersistentSkillCatalog(database, workspace)
    await database.initialize()
    source = tmp_path / "skill"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: report\ndescription: Reporting\n---\nUse scripts.")
    (source / "script.py").write_text("print('v1')")
    skill = await skills.publish("report", "1", source)
    await workspace.create("work")
    assert await skills.prepare((skill.ref,), "work", mode="lazy") == (skill,)
    assert not (await workspace.root_path("work") / ".agents").exists()
    (source / "script.py").write_text("print('v2')")
    await skills.load(skill.ref, "work")
    await skills.load(skill.ref, "work")
    assert await workspace.read("work", ".agents/report/script.py") == b"print('v1')"
    with pytest.raises(OpsMeshError, match="immutable"):
        await skills.publish("report", "1", source)


@pytest.mark.asyncio
async def test_mcp_locations_and_tool_allowlist_do_not_start_processes(
    database: AsyncDatabase,
) -> None:
    registry = MCPServerRegistry(database)
    await database.initialize()
    with pytest.raises(OpsMeshError):
        await registry.register(
            MCPServerDefinition(
                name="remote",
                version="1",
                transport="streamable_http",
                execution_location="host",
                url="https://example.com/mcp",
            )
        )

    async def secret(ref: str) -> str:
        raise AssertionError("Secrets should not be resolved for denied calls")

    connector = MCPServerConnector(secret, allow)
    definition = MCPServerDefinition(
        name="local",
        version="1",
        transport="stdio",
        execution_location="host",
        command="never-executed",
        allowed_tools=("read",),
    )
    with pytest.raises(OpsMeshError, match="not enabled"):
        await connector.call(definition, "read", {}, principal_id="alice")
    with pytest.raises(OpsMeshError, match="allowlist"):
        await connector.call(definition, "write", {}, principal_id="alice")


@pytest.mark.asyncio
async def test_mcp_stdio_real_transport_uses_a_local_test_server(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    script = tmp_path / "mcp_test_server.py"
    script.write_text(
        "try:\n"
        "    from mcp.server.mcpserver import MCPServer\n"
        "except ImportError:\n"
        "    from mcp.server.fastmcp import FastMCP as MCPServer\n"
        "server = MCPServer('test')\n"
        "@server.tool()\n"
        "def double(value: int) -> dict:\n"
        "    return {'value': value * 2}\n"
        "if __name__ == '__main__':\n"
        "    server.run(transport='stdio')\n",
        encoding="utf-8",
    )

    async def secret(ref: str) -> str:
        raise AssertionError("Test server does not need a secret")

    connector = MCPServerConnector(secret, allow, trusted_stdio=True)
    definition = MCPServerDefinition(
        name="test",
        version="1",
        transport="stdio",
        execution_location="host",
        command=sys.executable,
        args=(str(script),),
        allowed_tools=("double",),
    )
    discovered = await connector.discover(definition, principal_id="alice")
    assert [tool.name for tool in discovered] == ["double"]
    result = await connector.call(
        definition,
        "double",
        {"value": 21},
        principal_id="alice",
    )
    assert json.loads(result["content"][0]["text"]) == {"value": 42}


@pytest.mark.asyncio
async def test_resource_compiler_routes_skills_and_knowledge_through_gateway(
    database: AsyncDatabase, tmp_path: Path
) -> None:
    workspaces = LocalWorkspaceService(database, tmp_path / "data")
    catalog = PersistentCapabilityCatalog(database)
    code = InProcessCodeToolRegistry()
    skills = PersistentSkillCatalog(database, workspaces)
    servers = MCPServerRegistry(database)
    knowledge = LocalKnowledgeIndex(database, allow)
    invoker = AuthorizedToolInvoker(database, catalog, code, allow)

    async def secret(ref: str) -> str:
        raise AssertionError("No MCP credentials expected")

    compiler = AgentResourceCompiler(
        catalog,
        code,
        skills,
        servers,
        MCPServerConnector(secret, allow),
        knowledge,
        workspaces,
        allow,
    )
    await database.initialize()
    await workspaces.create("project")
    source = tmp_path / "skill"
    source.mkdir()
    (source / "SKILL.md").write_text("Read current invoices before answering.")
    await skills.publish("invoice", "1", source)
    await knowledge.add(
        KnowledgeDocument(id="doc", collection="finance", text="Invoices settle monthly."),
        principal_id="alice",
    )
    prepared = await compiler.prepare(
        AgentResourceRequest(
            skill_refs=("invoice@1",), workspace_id="project", knowledge_collections=("finance",)
        ),
        principal_id="alice",
        run_id="run1",
    )
    tools = {tool.name: tool for tool in prepared.tools}
    assert set(tools) == {"load_skill", "read_skill", "search_knowledge"}
    read_ref = tools["read_skill"].tool_ref
    search_ref = tools["search_knowledge"].tool_ref
    assert read_ref is not None and search_ref is not None
    result = await invoker.invoke(
        read_ref,
        {"skill_ref": "invoice@1", "path": "SKILL.md"},
        principal_id="alice",
        call_id="read1",
    )
    assert "invoices" in result["text"]
    hits = await invoker.invoke(
        search_ref,
        {"collection": "finance", "query": "invoices"},
        principal_id="alice",
        call_id="search1",
    )
    assert hits[0]["document"]["id"] == "doc"
    with pytest.raises(OpsMeshError):
        await invoker.invoke(
            read_ref,
            {"skill_ref": "invoice@1", "path": "../../secret"},
            principal_id="alice",
            call_id="invalid",
        )
    events = await database.pending_events()
    assert any(event["type"] == "capability.published" for event in events)
    request = AgentResourceRequest(
        skill_refs=("invoice@1",), workspace_id="project", knowledge_collections=("finance",)
    )
    await compiler.prepare(request, principal_id="alice", run_id="run1", lifecycle_id="new")
    await compiler.prepare(request, principal_id="alice", run_id="run1", lifecycle_id="new")
    definition = await catalog.get_tool(read_ref)
    await compiler.release(principal_id="alice", run_id="run1")
    assert code.resolve(definition.code_ref) is not None  # New attempt still owns the closure.
    await compiler.release(principal_id="alice", run_id="run1", lifecycle_id="new")
    with pytest.raises(OpsMeshError) as removed:
        code.resolve(definition.code_ref)
    assert removed.value.code == "NOT_FOUND"
    assert await catalog.get_tool(read_ref) == definition  # Persistent audit definition remains.
    await compiler.release(principal_id="alice", run_id="run1", lifecycle_id="new")


@pytest.mark.asyncio
async def test_knowledge_retrieval_is_collection_scoped_and_authorized(
    database: AsyncDatabase,
) -> None:
    knowledge = LocalKnowledgeIndex(database, allow)
    await database.initialize()
    await knowledge.add(
        KnowledgeDocument(id="a", collection="sales", text="Quarterly sales report"),
        principal_id="alice",
    )
    await knowledge.add(
        KnowledgeDocument(id="b", collection="private", text="Quarterly secret"),
        principal_id="alice",
    )
    hits = await knowledge.search("sales", "quarterly", principal_id="alice")
    assert [hit.document.id for hit in hits] == ["a"]
    with pytest.raises(OpsMeshError):
        await knowledge.search("sales", "quarterly", principal_id="bob")


@pytest.mark.asyncio
async def test_local_execution_output_timeout_cancel_and_resource_rejection(tmp_path: Path) -> None:
    provider = LocalExecutionProvider()
    with pytest.raises(OpsMeshError):
        await provider.create(ExecutionSpec(workspace=tmp_path))
    with pytest.raises(OpsMeshError, match="cannot enforce"):
        await provider.create(ExecutionSpec(workspace=tmp_path, trusted_local=True, memory_mb=100))
    session = await provider.create(ExecutionSpec(workspace=tmp_path, trusted_local=True))
    result = await session.execute(
        CommandRequest(id="one", argv=(sys.executable, "-c", "print('x'*200)"), max_output_bytes=30)
    )
    assert len(result.stdout) == 30 and result.output_truncated and result.exit_code == 0
    with pytest.raises(OpsMeshError, match="timeout"):
        await session.execute(
            CommandRequest(
                id="slow",
                argv=(sys.executable, "-c", "import time; time.sleep(5)"),
                timeout_seconds=0.1,
            )
        )
    task = asyncio.create_task(
        session.execute(
            CommandRequest(id="cancel", argv=(sys.executable, "-c", "import time; time.sleep(5)"))
        )
    )
    async with asyncio.timeout(2):
        while "cancel" not in session._processes:
            await asyncio.sleep(0.01)
    await session.cancel("cancel")
    with pytest.raises(OpsMeshError, match="cancelled"):
        await task
    await session.close()


@pytest.mark.asyncio
async def test_docker_builds_real_limits_without_launching_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opsmesh.modules.execution.internal import service

    calls: list[tuple[str, ...]] = []

    async def control(*args: str) -> str:
        calls.append(args)
        return "container123"

    monkeypatch.setattr(service, "docker_control", control)
    provider = DockerExecutionProvider()
    with pytest.raises(OpsMeshError, match="disk quotas"):
        await provider.create(
            ExecutionSpec(workspace=tmp_path, provider="docker", image="python", disk_mb=50)
        )
    session = await provider.create(
        ExecutionSpec(
            workspace=tmp_path,
            provider="docker",
            image="python",
            cpu=1.5,
            memory_mb=256,
            network="none",
        )
    )
    assert "--cpus" in calls[0] and "1.5" in calls[0]
    assert "--memory" in calls[0] and "256m" in calls[0] and "--network" in calls[0]
    await session.close()
    assert calls[-1] == ("rm", "--force", "container123")


@pytest.mark.asyncio
async def test_memory_two_stages_cas_idempotency_and_forget(
    database: AsyncDatabase, tmp_path: Path
) -> None:
    workspaces = LocalWorkspaceService(database, tmp_path / "data")
    invocations: list[str] = []
    observations: list[str] = []

    async def extract(source: MemorySource) -> MemoryExtraction:
        invocations.append("extract")
        return MemoryExtraction(
            source_id=source.id, summary="Preference", notes=source.text, usage={"tokens": 4}
        )

    async def consolidate(
        items: tuple[MemoryExtraction, ...], prior: MemoryCandidate
    ) -> MemoryCandidate:
        invocations.append("consolidate")
        return MemoryCandidate(
            summary="Use concise replies", index=items[0].notes, usage={"tokens": 5}
        )

    async def observe(job: str, stage: str, usage: dict[str, Any]) -> None:
        observations.append(stage)

    memory = PersistentMemoryService(
        database, workspaces, extract, consolidate, observe_usage=observe
    )
    await database.initialize()
    await memory.create_namespace(MemoryNamespace(id="project", owner="alice"))
    source = MemorySource(
        id="s1",
        namespace_id="project",
        conversation_id="c1",
        run_id="r1",
        text="Keep replies concise",
    )
    await memory.append_source(source, principal_id="alice")
    await memory.append_source(source, principal_id="alice")
    with pytest.raises(OpsMeshError):
        await memory.append_source(
            source.model_copy(update={"text": "changed"}), principal_id="alice"
        )
    first = await memory.request_generation("project", principal_id="alice", idempotency_key="job1")
    stale = await memory.request_generation("project", principal_id="alice", idempotency_key="job2")
    assert (await memory.run_job(first.id)).status == "succeeded"
    assert (await memory.run_job(first.id)).status == "succeeded"
    assert invocations == ["extract", "consolidate"]
    assert observations == ["extract", "consolidate"]
    assert (await memory.read("project", principal_id="alice")).index == source.text
    invalidated = await memory.run_job(stale.id)
    assert invalidated.status == "failed"
    assert invalidated.error_code == "MEMORY_NAMESPACE_CHANGED"
    assert stale.id not in {job.id for job in await memory.list_pending_jobs()}
    with pytest.raises(OpsMeshError):
        await memory.read("project", principal_id="bob")
    await memory.forget("project", principal_id="alice")
    with pytest.raises(OpsMeshError):
        await memory.read("project", principal_id="alice")


@pytest.mark.asyncio
async def test_failed_memory_does_not_publish_and_retry_reuses_extraction(
    database: AsyncDatabase, tmp_path: Path
) -> None:
    workspaces = LocalWorkspaceService(database, tmp_path / "data")
    extracts = 0
    attempts = 0

    async def extract(source: MemorySource) -> MemoryExtraction:
        nonlocal extracts
        extracts += 1
        return MemoryExtraction(source_id=source.id, summary="summary", notes="notes")

    async def consolidate(
        items: tuple[MemoryExtraction, ...], prior: MemoryCandidate
    ) -> MemoryCandidate:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("model failed")
        return MemoryCandidate(summary="summary", index="index")

    memory = PersistentMemoryService(database, workspaces, extract, consolidate)
    await database.initialize()
    await memory.create_namespace(MemoryNamespace(id="p", owner="alice"))
    await memory.append_source(
        MemorySource(id="s", namespace_id="p", conversation_id="c", run_id="r", text="hello"),
        principal_id="alice",
    )
    job = await memory.request_generation("p", principal_id="alice", idempotency_key="j")
    with pytest.raises(OpsMeshError):
        await memory.run_job(job.id)
    assert (await memory.read("p", principal_id="alice")).summary == ""
    assert (await memory.run_job(job.id)).status == "succeeded"
    assert extracts == 1
    await memory.create_namespace(
        MemoryNamespace(id="readonly", owner="alice", policy=MemoryPolicy(generate=False))
    )
    with pytest.raises(OpsMeshError):
        await memory.request_generation("readonly", principal_id="alice", idempotency_key="no")
