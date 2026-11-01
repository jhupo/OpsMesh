"""Skill tool receipts can be reused directly without weakening manifest boundaries."""

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.capabilities.api import AgentResourceRequest, ToolDefinition
from opsmesh.modules.capabilities.internal.catalog import PersistentCapabilityCatalog
from opsmesh.modules.capabilities.internal.code_tools import InProcessCodeToolRegistry
from opsmesh.modules.capabilities.internal.resource_compiler import AgentResourceCompiler
from opsmesh.modules.capabilities.internal.skills import PersistentSkillCatalog
from opsmesh.modules.capabilities.internal.tool_invoker import AuthorizedToolInvoker
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService


@pytest_asyncio.fixture
async def resources(tmp_path: Path) -> AsyncIterator[SimpleNamespace]:
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.sqlite'}")
    try:
        workspaces = LocalWorkspaceService(database, tmp_path / "data")
        skills = PersistentSkillCatalog(database, workspaces)
        catalog = PersistentCapabilityCatalog(database)
        code = InProcessCodeToolRegistry()
        authorize = AsyncMock()
        invoker = AuthorizedToolInvoker(database, catalog, code, authorize)
        compiler = AgentResourceCompiler(
            catalog, code, skills, AsyncMock(), AsyncMock(), AsyncMock(), workspaces, authorize
        )
        await database.initialize()
        await workspaces.create("project")
        for name, asset in (("invoice", "references/invoices.md"), ("expenses", "rates.csv")):
            source = tmp_path / name
            source.mkdir()
            (source / "SKILL.md").write_text(f"Instructions for {name}.", encoding="utf-8")
            (source / asset).parent.mkdir(parents=True, exist_ok=True)
            (source / asset).write_text(f"Declared asset for {name}.", encoding="utf-8")
            await skills.publish(name, "1", source)
        request = AgentResourceRequest(
            skill_refs=("invoice@1", "expenses@1"), workspace_id="project"
        )
        prepared = await compiler.prepare(request, principal_id="alice", run_id="run")
        yield SimpleNamespace(
            skills=skills,
            catalog=catalog,
            code=code,
            compiler=compiler,
            request=request,
            tools={tool.name: tool for tool in prepared.tools},
            invoker=invoker,
        )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_load_receipt_supplies_readable_entry_and_declared_assets(resources):
    loaded = await resources.invoker.invoke(
        resources.tools["load_skill"].tool_ref,
        {"skill_ref": "invoice@1"},
        principal_id="alice",
        call_id="load-invoice",
    )
    assert loaded["skill_relative_entry"] == "SKILL.md"
    assert loaded["path"] == ".agents/invoice/SKILL.md"  # Existing callers retain location info.
    assert loaded["declared_assets"] == ["SKILL.md", "references/invoices.md"]
    entry = await resources.invoker.invoke(
        resources.tools["read_skill"].tool_ref,
        loaded["read_skill_arguments"],
        principal_id="alice",
        call_id="read-entry",
    )
    assert entry["text"] == "Instructions for invoice."
    for index, path in enumerate(loaded["declared_assets"]):
        result = await resources.invoker.invoke(
            resources.tools["read_skill"].tool_ref,
            {"skill_ref": loaded["skill_ref"], "path": path},
            principal_id="alice",
            call_id=f"read-asset-{index}",
        )
        assert result["path"] == path and "invoice" in result["text"]
    schema = resources.tools["read_skill"].input_schema
    assert schema["properties"]["path"]["enum"] == [
        "SKILL.md",
        "rates.csv",
        "references/invoices.md",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        ".agents/invoice/SKILL.md",
        "skills/invoice/SKILL.md",
        "/SKILL.md",
        "C:/SKILL.md",
        "../SKILL.md",
        "../../secret",
        "references/../SKILL.md",
        "references\\invoices.md",
        ".agents/expenses/rates.csv",
        "undeclared.md",
    ],
)
async def test_workspace_absolute_and_undeclared_paths_fail_schema_before_loading(
    resources, monkeypatch: pytest.MonkeyPatch, path: str
):
    load = AsyncMock(side_effect=AssertionError("Invalid path must not reach skill loading"))
    monkeypatch.setattr(resources.skills, "load", load)
    with pytest.raises(OpsMeshError) as error:
        await resources.invoker.invoke(
            resources.tools["read_skill"].tool_ref,
            {"skill_ref": "invoice@1", "path": path},
            principal_id="alice",
            call_id="invalid-path",
        )
    assert error.value.code == "INPUT_INVALID"
    load.assert_not_awaited()


@pytest.mark.asyncio
async def test_asset_declared_by_another_bound_skill_is_still_denied(resources, monkeypatch):
    load = AsyncMock(side_effect=AssertionError("Cross-skill asset must not be loaded"))
    monkeypatch.setattr(resources.skills, "load", load)
    with pytest.raises(OpsMeshError) as error:
        await resources.invoker.invoke(
            resources.tools["read_skill"].tool_ref,
            {"skill_ref": "invoice@1", "path": "rates.csv"},
            principal_id="alice",
            call_id="foreign-asset",
        )
    assert error.value.code == "AUTHORIZATION_DENIED"
    load.assert_not_awaited()


@pytest.mark.asyncio
async def test_unbound_skill_is_rejected_by_schema(resources):
    with pytest.raises(OpsMeshError) as error:
        await resources.invoker.invoke(
            resources.tools["read_skill"].tool_ref,
            {"skill_ref": "private@1", "path": "SKILL.md"},
            principal_id="alice",
            call_id="unbound-skill",
        )
    assert error.value.code == "INPUT_INVALID"


@pytest.mark.asyncio
async def test_upgrade_keeps_legacy_run_tool_versions_immutable(resources):
    request = resources.request
    old_scope = hashlib.sha256(
        f"alice:upgrade-run:{request.model_dump_json()}".encode()
    ).hexdigest()[:24]
    legacy = ToolDefinition(
        name="read_skill",
        version=old_scope,
        description="Read SKILL.md or a declared skill asset",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        code_ref=f"resource:read_skill:{old_scope}",
    )
    await resources.catalog.register_tool(legacy)
    prepared = await resources.compiler.prepare(request, principal_id="alice", run_id="upgrade-run")
    current = next(tool for tool in prepared.tools if tool.name == "read_skill")
    assert current.tool_ref != legacy.ref
    assert await resources.catalog.get_tool(legacy.ref) == legacy
    result = await resources.invoker.invoke(
        current.tool_ref,
        {"skill_ref": "invoice@1", "path": "SKILL.md"},
        principal_id="alice",
        call_id="upgraded-read",
    )
    assert result["text"] == "Instructions for invoice."
