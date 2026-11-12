"""Run after the isolated chat plugin bootstrap in test_example_chat."""

import asyncio
import json
import os
import runpy
import tempfile
from pathlib import Path
from types import SimpleNamespace

from agents import RunConfig, Runner
from agents.testing import ModelStep, ScriptedModel, assistant_message
from alembic.migration import MigrationContext
from alembic.operations import Operations
from example_chat import connections as chat_connections
from example_chat import lifecycle
from example_chat.agent import agent
from example_chat.sessions import ChatConversations, ConversationError
from sqlalchemy.ext.asyncio import create_async_engine

from opsmesh.framework import runtime
from opsmesh.framework.dispatch import dispatch_event
from opsmesh.framework.drivers import Request
from opsmesh.framework.plugins.model import PluginStatus
import opsmesh_execution as execution
from opsmesh_channels import UserContext
from opsmesh_channels.api import (
    APIChannel,
    APIRouter,
    HTTPError,
    Settings,
)
from opsmesh_channels.api.adapter import APIAdapter
from opsmesh_channels.qq.events import QQMessageEvent


def run_scenario(plugin, routes, connections):
    """Exercise conversations using real SDK history and framework dispatch."""
    plugin.status = PluginStatus.READY
    channel = connections._adapter.declarations[0]
    responses = []
    caller = UserContext(id="verified-user")

    async def send(event, message):
        responses.append(str(message))

    channel.send = send

    async def run_native(selected_agent, message, **options):
        assert selected_agent is agent
        assert options["context"] == caller
        assert (
            options["trace_context"].session_id == options["session"].session_id
        )
        assert "run_config" not in options
        return await Runner.run(
            selected_agent,
            message,
            context=options["context"],
            session=options["session"],
            run_config=RunConfig(tracing_disabled=True),
        )

    execution.run = run_native
    agent.model = ScriptedModel(
        [
            ModelStep(output=[assistant_message(text)])
            for text in [
                "bot answer",
                "remembered",
                "continued",
                "separate",
                "after clear",
            ]
        ]
    )

    async def dispatch(content):
        event = QQMessageEvent(
            connection_id=channel.self_id,
            user_id="verified-user",
            message_id="message",
            event_id="event",
            content=content,
            raw={},
        )
        outcome = await dispatch_event(
            channel,
            event,
            invocation_context=caller,
            matcher_filter=channel.accepts_matcher,
        )
        outcome.raise_for_errors()

    api_channel = APIChannel(
        Settings(identities=[{"id": "test", "token_ref": "TEST_TOKEN"}]),
        APIRouter(),
    )
    api_channel.adapter = APIAdapter(runtime.get_driver())

    async def invoke(method, path, data=None, session_id=None, owner=None):
        selected = next(
            item
            for item in routes
            if item.method == method and item.relative_path == path
        )
        request = Request(
            method,
            "http://localhost" + selected.path,
            content=json.dumps(data).encode() if data is not None else None,
            path_params={"id": session_id} if session_id else {},
        )
        result = await api_channel._invoke(
            selected, request, SimpleNamespace(caller=owner or caller)
        )
        return json.loads(result.content) if result.status_code != 204 else None

    async def expect_error(operation, status):
        try:
            await operation
        except (HTTPError, ConversationError) as error:
            assert (
                getattr(error, "status_code", getattr(error, "status", None))
                == status
            )
        else:
            raise AssertionError("Expected rejection")

    def migrate(connection):
        namespace = runpy.run_path(
            "examples/chat/example_chat/migrations/0001_example_chat.py"
        )
        with Operations.context(MigrationContext.configure(connection)):
            namespace["upgrade"]()

    async def verify(engine):
        async with engine.begin() as connection:
            await connection.run_sync(migrate)
        store = ChatConversations(engine)
        lifecycle._conversations = store
        await chat_connections.connect_mcp()
        await dispatch("/help")
        assert responses[-1].startswith("/chat <question>")
        await dispatch("/chat")
        assert responses[-1].startswith("Usage:")
        await dispatch("/chat " + "x" * 8001)
        assert responses[-1].startswith("Usage:")
        assert len(agent.model.calls) == 0
        await dispatch("/chat Explain Python")
        assert responses[-1] == "bot answer"
        before = list(responses)
        await dispatch("ordinary text")
        await dispatch("/unknown ignored")
        assert responses == before

        first = await invoke("POST", "/sessions", {"name": "First"})
        sid = first["id"]
        second = await invoke("POST", "/sessions", {"name": "Second"})
        assert sid != second["id"]
        empty = await invoke("GET", "/sessions/{id}/messages", session_id=sid)
        assert empty == []
        await invoke(
            "POST", "/chat", {"session_id": sid, "message": "Remember amber"}
        )
        result = await invoke(
            "POST", "/chat", {"session_id": sid, "message": "What color?"}
        )
        assert result == {"text": "continued", "session_id": sid}
        assert "Remember amber" in json.dumps(agent.model.calls[2].input)
        await invoke(
            "POST", "/chat", {"session_id": second["id"], "message": "Separate"}
        )
        assert "Remember amber" not in json.dumps(agent.model.calls[3].input)

        for command in ["/help", "/session", "/history"]:
            response = await invoke(
                "POST", "/chat", {"session_id": sid, "message": command}
            )
            assert response["session_id"] == sid
        assert len(agent.model.calls) == 4
        await expect_error(
            invoke("POST", "/chat", {"session_id": sid, "message": "/unknown"}),
            400,
        )
        await expect_error(
            invoke(
                "POST",
                "/chat",
                {"session_id": sid, "message": "secret"},
                owner=UserContext(id="other"),
            ),
            404,
        )
        for method, path in [
            ("GET", "/sessions/{id}"),
            ("GET", "/sessions/{id}/messages"),
            ("DELETE", "/sessions/{id}/messages"),
            ("DELETE", "/sessions/{id}"),
        ]:
            await expect_error(
                invoke(
                    method,
                    path,
                    session_id=sid,
                    owner=UserContext(id="other"),
                ),
                404,
            )
        assert (
            await invoke("GET", "/sessions", owner=UserContext(id="other"))
            == []
        )

        async with store.hold(caller.id, sid):
            await expect_error(
                invoke(
                    "POST", "/chat", {"session_id": sid, "message": "blocked"}
                ),
                409,
            )
            await expect_error(
                invoke("DELETE", "/sessions/{id}/messages", session_id=sid), 409
            )
            await expect_error(
                invoke("DELETE", "/sessions/{id}", session_id=sid), 409
            )
            assert await store.get(caller.id, second["id"])
        await invoke("POST", "/chat", {"session_id": sid, "message": "/clear"})
        assert (
            await invoke("GET", "/sessions/{id}/messages", session_id=sid) == []
        )
        assert (await invoke("GET", "/sessions/{id}", session_id=sid))[
            "name"
        ] == "First"
        await invoke("POST", "/chat", {"session_id": sid, "message": "Fresh"})
        assert "Remember amber" not in json.dumps(agent.model.calls[4].input)

        if engine.dialect.name == "postgresql":
            competing = ChatConversations(engine)
            async with store.hold(caller.id, sid):
                await expect_error(competing.clear(caller.id, sid), 409)

        await engine.dispose()
        lifecycle._conversations = ChatConversations(engine)
        assert (
            len(await invoke("GET", "/sessions/{id}/messages", session_id=sid))
            == 2
        )
        assert (await invoke("GET", "/sessions/{id}", session_id=second["id"]))[
            "id"
        ] == second["id"]
        locked = asyncio.Event()

        async def cancelled_owner():
            async with lifecycle._conversations.hold(caller.id, sid):
                locked.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(cancelled_owner())
        await locked.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        async with lifecycle._conversations.hold(caller.id, sid):
            pass

        pending = lifecycle._conversations
        original_history = pending.history

        async def fail_clear():
            raise RuntimeError("Injected history cleanup failure")

        pending.history = lambda key: SimpleNamespace(clear_session=fail_clear)
        try:
            await pending.delete(caller.id, sid)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected deletion interruption")
        finally:
            pending.history = original_history
        assert (await pending.get(caller.id, sid)).deleting
        await expect_error(
            invoke(
                "POST",
                "/chat",
                {"session_id": sid, "message": "blocked deletion"},
            ),
            409,
        )
        await invoke("DELETE", "/sessions/{id}", session_id=sid)
        await expect_error(invoke("GET", "/sessions/{id}", session_id=sid), 404)
        assert await store.history(sid).get_items() == []
        agent.model.assert_complete()
        await chat_connections.disconnect_mcp()
        assert agent.mcp_servers == []

    async def main():
        with tempfile.TemporaryDirectory() as directory:
            engine = create_async_engine(
                os.environ.get("OPSMESH_TEST_DATABASE_URL")
                or "sqlite+aiosqlite:///" + str(Path(directory) / "chat.sqlite")
            )
            try:
                await verify(engine)
            finally:
                lifecycle._conversations = None
                await engine.dispose()

    asyncio.run(main())
    print("conversations and commands verified")
