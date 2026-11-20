"""Run against installed wheels and a dedicated pgvector database."""

import asyncio
import json
from uuid import uuid4

import opsmesh
from opsmesh.plugin.config import prepare_plugins


async def check_knowledge():
    """Exercise a real consumer and clean up its own test library."""
    from agents.tool_context import ToolContext

    knowledge = opsmesh.require("knowledge")
    tools = knowledge.build_tools(knowledge.ToolSettings(alias="smoke"))
    by_name = {tool.name: tool for tool in tools}

    async def call(operation, **arguments):
        tool = by_name["smoke_" + operation]
        encoded = json.dumps(arguments)
        context = ToolContext(
            context=None,
            tool_name=tool.name,
            tool_call_id="smoke",
            tool_arguments=encoded,
        )
        result = await tool.on_invoke_tool(context, encoded)
        assert isinstance(result, dict), (operation, result)
        return result

    assert len(tools) == 11
    assert by_name["smoke_add_document"].needs_approval
    assert not by_name["smoke_search_knowledge"].needs_approval
    created = await call("create_knowledge_base", name=f"Smoke {uuid4().hex}")
    scope = {"knowledge_base_id": created["knowledge_base_id"]}
    library = await knowledge.get(created["knowledge_base_id"])
    try:
        source = "The support desk opens at nine on weekdays."
        item = await call(
            "add_document", **scope, name="hours.txt", text=source
        )
        doc = {**scope, "document_id": item["id"]}
        async with asyncio.timeout(90):
            while item["status"] != "ready":
                assert item["status"] != "failed", item["error"]
                await asyncio.sleep(0.5)
                item = await call("get_document", **doc)
        found = await call("search_knowledge", **scope, query="support hours")
        hit = found["hits"][0]
        assert hit["document_id"] == item["id"]
        result = await call("read_document", **doc, index_key=hit["index_key"])
        assert result["text"] == source
        assert await call("delete_document", **doc) == {"deleted": True}
        assert await call("list_documents", **scope) == {"documents": []}
    finally:
        await library.delete()


async def main():
    """Use the current framework loader and driver lifecycle."""
    from importlib import import_module

    opsmesh.init(driver="~none")
    for name in ("database", "filesystem", "knowledge", "knowledge_smoke"):
        plugin = opsmesh.load_plugin(name)
        assert plugin is not None, name
        plugin.manager.enable_plugin(plugin)
    driver = opsmesh.get_driver()
    prepare_plugins(driver, opsmesh.get_loaded_plugins(), ())
    await import_module("database.commands").migrate()
    async with driver._lifespan:
        await opsmesh.require("knowledge_smoke").check_knowledge()
    print("Knowledge wheel smoke passed: migration, indexing, search, cleanup")


if __name__ == "__main__":
    asyncio.run(main())
