"""Native SDK and storage checks for the unified knowledge tool surface."""

# ruff: noqa: E402

import json
import sys
from contextlib import asynccontextmanager
from contextvars import ContextVar
from importlib import import_module
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

pytest.importorskip("agents.testing", exc_type=ImportError)
pytest.importorskip("aiosqlite", exc_type=ImportError)
pytest.importorskip("pgvector.sqlalchemy", exc_type=ImportError)

from agents import Agent, RunConfig, Runner
from agents.testing import ScriptedModel
from agents.testing.model import ModelStep, assistant_message
from agents.tool_context import ToolContext
from openai.types.responses import ResponseFunctionToolCall
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


@pytest.fixture
async def knowledge_tools(tmp_path, monkeypatch):
    # Replace the legacy provider boundaries, not the tools or storage queries.
    namespace = "_knowledge_tool_test"
    package = ModuleType(namespace)
    package.__path__ = [
        str(Path(__file__).resolve().parents[2] / "packages/knowledge/src")
    ]
    monkeypatch.setitem(sys.modules, namespace, package)
    caller = ContextVar("caller", default=None)

    class Base(DeclarativeBase):
        pass

    database = ModuleType("database")
    database.Base = Base
    monkeypatch.setitem(sys.modules, database.__name__, database)
    lifecycle = ModuleType(f"{namespace}.lifecycle")
    monkeypatch.setitem(sys.modules, lifecycle.__name__, lifecycle)
    settings = import_module(f"{namespace}.config").Settings
    bases = import_module(f"{namespace}.knowledge_bases")
    monkeypatch.setattr(
        bases, "get_plugin_by_module_name", lambda _: caller.get()
    )
    tools = import_module(f"{namespace}.tools")
    errors = import_module(f"{namespace}.errors")
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = column.type.with_variant(JSON(), "sqlite")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def access():
        yield

    async def write_source(path, content):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    async def remove_source(path):
        (tmp_path / path).unlink(missing_ok=True)

    runtime = SimpleNamespace(
        settings=settings(),
        profile={"fingerprint": "test"},
        database=SimpleNamespace(session=sessions, transaction=sessions.begin),
        access=access,
        write_source=write_source,
        remove_source=remove_source,
        discard_source=remove_source,
    )
    lifecycle.get_runtime = lambda: runtime
    caller.set(SimpleNamespace(id_="consumer"))
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield SimpleNamespace(
            caller=caller,
            tools=tools,
            bases=bases,
            runtime=runtime,
            errors=errors,
        )
    finally:
        await engine.dispose()
        for name in tuple(sys.modules):
            if name.startswith(f"{namespace}.") and name != lifecycle.__name__:
                sys.modules.pop(name)


async def invoke(tool, **arguments):
    data = json.dumps(arguments)
    context = ToolContext(
        context=None,
        tool_name=tool.name,
        tool_call_id="test",
        tool_arguments=data,
    )
    return await tool.on_invoke_tool(context, data)


@pytest.mark.anyio
async def test_tool_names_schema_and_policy(knowledge_tools):
    fixture = knowledge_tools
    defaults = fixture.tools.build_tools()
    all_operations = {
        "create_knowledge_base",
        "list_knowledge_bases",
        "get_knowledge_base",
        "search_knowledge",
        "list_documents",
        "get_document",
        "read_document",
        "add_document",
        "replace_document",
        "delete_document",
        "reindex_document",
    }
    writes = {
        "create_knowledge_base",
        "add_document",
        "replace_document",
        "delete_document",
        "reindex_document",
    }
    assert {tool.name for tool in defaults} == {
        f"knowledge_{name}" for name in all_operations
    }
    assert {tool.name for tool in defaults if tool.needs_approval} == {
        f"knowledge_{name}" for name in writes
    }

    settings = type(fixture.runtime.settings)
    fixture.runtime.settings = settings.model_validate(
        {"tools": {"alias": "support", "approval": {"add_document": False}}}
    )
    configured = {tool.name: tool for tool in fixture.tools.build_tools()}
    assert not configured["support_add_document"].needs_approval
    assert configured["support_delete_document"].needs_approval
    options = fixture.tools.ToolSettings(
        alias="review", approval={"read_document": True}
    )
    overrides = {tool.name: tool for tool in fixture.tools.build_tools(options)}
    assert overrides["review_read_document"].needs_approval
    assert overrides["review_add_document"].needs_approval
    properties = overrides["review_read_document"].params_json_schema[
        "properties"
    ]
    assert set(properties) == {
        "knowledge_base_id",
        "document_id",
        "start",
        "end",
        "index_key",
    }
    assert properties["knowledge_base_id"]["format"] == "uuid"
    assert (
        len(fixture.tools.unique_tools(defaults, list(overrides.values())))
        == 22
    )
    with pytest.raises(ValueError, match="unique"):
        fixture.tools.unique_tools(defaults, defaults)
    fixture.caller.set(None)
    with pytest.raises(fixture.errors.KnowledgeNotFound):
        fixture.tools.build_tools()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "options",
    [
        {"alias": "no-dashes"},
        {"alias": "a" * 41},
        {"approval": {"write_everything": False}},
        {"approval": {"add_document": "false"}},
    ],
)
async def test_invalid_tool_policy_is_rejected(knowledge_tools, options):
    with pytest.raises(ValidationError):
        knowledge_tools.tools.ToolSettings.model_validate(options)


@pytest.mark.anyio
async def test_tools_bind_owner_and_manage_documents(knowledge_tools):
    fixture = knowledge_tools
    tools = {tool.name: tool for tool in fixture.tools.build_tools()}

    async def call(operation, **arguments):
        return await invoke(tools[f"knowledge_{operation}"], **arguments)

    fixture.caller.set(SimpleNamespace(id_="other"))
    other = await fixture.bases.create("Other library")
    fixture.caller.set(None)
    created = await call("create_knowledge_base", name="Support")
    scope = {"knowledge_base_id": created["knowledge_base_id"]}
    assert UUID(scope["knowledge_base_id"]) != other.id
    entries = await call("list_knowledge_bases", limit=1)
    assert [entry["id"] for entry in entries["knowledge_bases"]] == [
        scope["knowledge_base_id"]
    ]
    assert (await call("get_knowledge_base", **scope))["name"] == "Support"
    foreign = {"knowledge_base_id": str(other.id)}
    assert (
        await call("get_knowledge_base", **foreign)
        == "Knowledge base was not found"
    )
    assert await call("search_knowledge", **foreign, query="hours") == (
        "Knowledge base was not found"
    )
    assert await call(
        "add_document", **foreign, name="denied", text="Text"
    ) == ("Knowledge base was not found")

    added = await call(
        "add_document", **scope, name="hours.md", text="Hours: 9"
    )
    assert added["status"] == "pending"
    listed = (await call("list_documents", **scope))["documents"]
    assert [item["id"] for item in listed] == [added["id"]]
    document = {**scope, "document_id": added["id"]}
    assert await call("get_document", **document) == listed[0]
    assert (await call("read_document", **document, start=7, end=8))[
        "text"
    ] == "9"
    replaced = await call("replace_document", **document, text="Hours: 8")
    assert replaced["index_key"] != added["index_key"]
    assert await call(
        "read_document", **document, index_key=added["index_key"]
    ) == ("The cited document index is no longer current")
    reindexed = await call("reindex_document", **document)
    assert reindexed["status"] == "pending"
    assert reindexed["index_key"] != replaced["index_key"]
    assert await call("delete_document", **document) == {"deleted": True}
    assert await call("list_documents", **scope) == {"documents": []}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("requires_approval", "approved"),
    [(True, True), (True, False), (False, None)],
)
async def test_creation_follows_sdk_approval_policy(
    knowledge_tools, requires_approval, approved
):
    fixture = knowledge_tools
    policy = (
        None
        if requires_approval
        else fixture.tools.ToolSettings(
            approval={"create_knowledge_base": False}
        )
    )
    tool = next(
        tool
        for tool in fixture.tools.build_tools(policy)
        if tool.name == "knowledge_create_knowledge_base"
    )
    model = ScriptedModel(
        [
            ModelStep(
                output=[
                    ResponseFunctionToolCall(
                        id="fc_create",
                        call_id="call_create",
                        type="function_call",
                        name=tool.name,
                        arguments=json.dumps(
                            {"name": "Support", "description": ""}
                        ),
                    )
                ]
            ),
            ModelStep(output=[assistant_message("Done.")]),
        ]
    )
    agent = Agent(name="Approver", model=model, tools=[tool])
    options = RunConfig(tracing_disabled=True)
    result = await Runner.run(agent, "Create Support", run_config=options)
    if requires_approval:
        assert len(result.interruptions) == 1
        assert await fixture.bases.list() == []
        state = result.to_state()
        decision = state.approve if approved else state.reject
        decision(result.interruptions[0])
        result = await Runner.run(agent, state, run_config=options)
    assert not result.interruptions
    assert [base.name for base in await fixture.bases.list()] == (
        [] if approved is False else ["Support"]
    )
