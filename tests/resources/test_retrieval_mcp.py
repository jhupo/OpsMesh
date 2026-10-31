"""Vector-provider swaps and real temporary local MCP SSE transport."""

import asyncio
import socket
import sys
from contextlib import asynccontextmanager

import pytest
import uvicorn

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.plugin import PluginManager
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.capabilities.api import KnowledgeDocument, MCPServerDefinition
from opsmesh.modules.capabilities.internal.knowledge import LocalKnowledgeIndex
from opsmesh.modules.capabilities.internal.mcp import MCPServerConnector


async def authorize(principal, resource):
    if principal != "alice":
        raise OpsMeshError("AUTHORIZATION_DENIED", "Denied")


class SynonymEmbedding:
    id = "test-semantic-v1"
    dimensions = 2

    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.extend(texts)
        return tuple(
            (1.0, 0.0) if "car" in text or "automobile" in text else (0.0, 1.0) for text in texts
        )


async def test_vector_hybrid_provider_extension_and_persistent_cache(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}")
    embedding = SynonymEmbedding()
    index = LocalKnowledgeIndex(database, authorize, embeddings=embedding)
    await database.initialize()
    try:
        await index.add(
            KnowledgeDocument(id="car", collection="public", text="car"), principal_id="alice"
        )
        await index.add(
            KnowledgeDocument(id="boat", collection="public", text="boat"), principal_id="alice"
        )
        await index.add(
            KnowledgeDocument(id="secret", collection="private", text="automobile"),
            principal_id="alice",
        )
        assert not await index.search("public", "automobile", mode="lexical", principal_id="alice")
        hits = await index.search("public", "automobile", mode="vector", principal_id="alice")
        assert [hit.document.id for hit in hits] == ["car"]
        assert hits[0].vector_score == 1 and hits[0].lexical_score == 0
        hybrid = await index.search("public", "car", principal_id="alice")
        assert hybrid[0].document.id == "car" and hybrid[0].lexical_score > 0
        embedding.calls.clear()
        reopened = LocalKnowledgeIndex(database, authorize, embeddings=embedding)
        await reopened.search("public", "automobile", principal_id="alice", mode="vector")
        assert embedding.calls == ["automobile"]  # Document vectors survived reconstruction.
        with pytest.raises(OpsMeshError):
            await reopened.search("private", "automobile", principal_id="bob")
        assert embedding.calls == ["automobile"]  # Authorization precedes provider calls.
    finally:
        await database.close()


async def test_default_vector_is_deterministic_and_invalid_provider_rejected(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}")
    index = LocalKnowledgeIndex(database, authorize)
    await database.initialize()
    try:
        await index.add(
            KnowledgeDocument(id="one", collection="docs", text="agent scheduling jobs"),
            principal_id="alice",
        )
        assert (await index.search("docs", "scheduling", principal_id="alice"))[
            0
        ].document.id == "one"
        first = await index.embeddings.embed(("repeatable vectors",))
        assert await index.embeddings.embed(("repeatable vectors",)) == first
        provider = SynonymEmbedding()
        provider.dimensions = 3
        broken = LocalKnowledgeIndex(database, authorize, embeddings=provider)
        with pytest.raises(OpsMeshError) as failure:
            await broken.search("docs", "car", principal_id="alice")
        assert failure.value.code == "EMBEDDING_INVALID"
    finally:
        await database.close()


async def test_plugin_embedding_ownership_rollback_disable_and_freeze(tmp_path, monkeypatch):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'owned.db'}")
    index = LocalKnowledgeIndex(database, authorize)
    manager = PluginManager()
    monkeypatch.setattr(sys, "_test_embedding_index", index, raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    source = (
        "import sys\n"
        "class Provider:\n"
        "    id='plugin-v1'\n"
        "    dimensions=2\n"
        "    async def embed(self,texts): return tuple((1.0,0.0) for _ in texts)\n"
        "sys._test_embedding_index.register_embedding_provider(Provider(),default=True)\n"
    )
    (tmp_path / "broken_embedding.py").write_text(
        source + "raise RuntimeError('load failed')\n", encoding="utf-8"
    )
    (tmp_path / "owned_embedding.py").write_text(source, encoding="utf-8")
    await database.initialize()
    try:
        with pytest.raises(RuntimeError):
            manager.load("broken_embedding")
        assert index.embeddings.id.startswith("local-hash")
        manager.load("owned_embedding")
        await index.add(
            KnowledgeDocument(id="one", collection="docs", text="anything"), principal_id="alice"
        )
        index.freeze()
        assert (await index.search("docs", "query", principal_id="alice"))[0].vector_score == 1
        manager.disable("owned_embedding")
        with pytest.raises(OpsMeshError) as disabled:
            await index.search("docs", "query", principal_id="alice")
        assert disabled.value.code == "PLUGIN_DISABLED"
        with pytest.raises(OpsMeshError) as frozen:
            index.register_embedding_provider(SynonymEmbedding())
        assert frozen.value.code == "REGISTRY_FROZEN"
    finally:
        for name in ("broken_embedding", "owned_embedding"):
            sys.modules.pop(name, None)
        await database.close()


@asynccontextmanager
async def local_server(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="none"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        listener.close()


async def test_mcp_sse_discovers_and_calls_real_local_server():
    pytest.importorskip("mcp")
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError:
        from mcp.server.fastmcp import FastMCP as MCPServer
    server = MCPServer("opsmesh-local-sse")

    @server.tool()
    async def echo(text: str) -> str:
        if text == "fail":
            raise ValueError("server-side private diagnostic")
        return "echo:" + text

    async def secret(ref):
        raise AssertionError("This local server has no secrets")

    connector = MCPServerConnector(secret, authorize)
    async with local_server(server.sse_app()) as url:
        definition = MCPServerDefinition(
            name="local",
            version="1",
            transport="sse",
            execution_location="remote",
            url=url + "/sse",
            allowed_tools=("echo",),
            timeout_seconds=5,
        )
        tools = await connector.discover(definition, principal_id="alice")
        assert len(tools) == 1 and tools[0].name == "echo"
        result = await connector.call(definition, "echo", {"text": "hello"}, principal_id="alice")
        assert result["content"][0]["text"] == "echo:hello"
        with pytest.raises(OpsMeshError) as tool_failure:
            await connector.call(definition, "echo", {"text": "fail"}, principal_id="alice")
        assert tool_failure.value.code == "TOOL_EXECUTION_FAILED"
        assert "private diagnostic" not in str(tool_failure.value)
        with pytest.raises(OpsMeshError) as forbidden:
            await connector.call(definition, "unlisted", {}, principal_id="alice")
        assert forbidden.value.code == "AUTHORIZATION_DENIED"
