"""Exercise example commands through native SDK history and channel dispatch."""

import asyncio
import json
import os
import runpy
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
from agents.testing import ModelStep, ScriptedModel, assistant_message
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import create_async_engine

from opsmesh.plugins.channels import UserContext
from opsmesh.plugins.channels.qq.events import parse_callback as qq_callback


class MessageHarness:
    def __init__(self, channel, user_id):
        self.channel = channel
        self.user_id = user_id
        self.caller = UserContext.scoped(channel.self_id, user_id)
        self.conversation_id = str(uuid4())

    async def post(self, text, **options):
        data = {
            "user": {"id": options.get("user", self.user_id)},
            "message": text,
            "conversation_id": options.get(
                "conversation_id", self.conversation_id
            ),
        }
        headers = (
            {"accept": "text/event-stream"} if options.get("stream") else {}
        )
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{self.channel.server.bound_port}",
            trust_env=False,
        ) as client:
            response = await client.post(
                f"/plugins/{self.channel.owner_id}/messages",
                json=data,
                headers=headers,
            )
        if not options.get("stream"):
            result = response.json()
            assert result.get("status") == "completed", (
                response.status_code,
                result,
            )
            assert result["conversation_id"] == data["conversation_id"]
            return result
        events = []
        for frame in response.text.split("\n\n"):
            if frame.strip():
                lines = frame.splitlines()
                events.append(
                    (
                        lines[0].removeprefix("event: "),
                        json.loads(lines[1].removeprefix("data: ")),
                    )
                )
        return events


def data_of(response):
    return response["messages"][-1]["data"]


def text_of(response):
    return "".join(
        part.get("text", "") for part in response["messages"][-1]["content"]
    )


async def command_error(operation, status):
    result = await operation
    assert data_of(result)["error"]["status"] == status, result


def migrate(connection):
    namespace = runpy.run_path(
        "examples/chat/example_chat/migrations/0001_example_chat.py"
    )
    with Operations.context(MigrationContext.configure(connection)):
        namespace["upgrade"]()


async def run_scenario(plugin, api_channel):
    from example_chat import agent as definition
    from example_chat import connections as chat_connections
    from example_chat import handlers, lifecycle
    from example_chat.agent import agent
    from example_chat.sessions import ChatConversations, ConversationError

    from opsmesh.dispatch import dispatch_event
    from opsmesh.permissions import Permission
    from opsmesh.plugins.channels.dingtalk.events import parse_callback
    from opsmesh.plugins.execution import Settings as ExecutionSettings
    from opsmesh.plugins.execution import _lifecycle as execution_lifecycle

    api = MessageHarness(api_channel, "verified-user")
    caller = api.caller
    agent.model = ScriptedModel(
        [
            ModelStep(output=[assistant_message(text)])
            for text in [
                "bot answer",
                "remembered",
                "continued",
                "separate",
                "after clear",
                "stream answer",
            ]
        ]
    )
    channel = api_channel.adapter.driver.get_adapter("qq").get_connection(
        plugin.id_, "primary"
    )
    responses = []

    async def send(event, message):
        responses.append(str(message))

    channel.send = send

    async def call(operation, **options):
        from opsmesh.plugins.channels.streaming import send_stream

        assert operation == "stream"

        async def complete(message, identifier):
            await send(options["event"], message)

        await send_stream(options["source"], complete)

    channel.call = call

    async def dispatch(content):
        event = qq_callback(
            {
                "op": 0,
                "t": "C2C_MESSAGE_CREATE",
                "d": {
                    "id": "message",
                    "content": content,
                    "author": {"user_openid": api.user_id},
                },
            },
            connection_id=channel.self_id,
        )
        outcome = await dispatch_event(
            channel,
            event,
            invocation_context=caller,
            matcher_filter=channel.accepts_matcher,
        )
        outcome.raise_for_errors()

    async def verify(engine):
        definition.settings = definition.settings.model_copy(
            update={"publication": "progressive"}
        )
        async with engine.begin() as connection:
            await connection.run_sync(migrate)
        store = ChatConversations(engine)
        lifecycle._conversations = store

        for command in [
            "/help",
            "/sessions",
            "/session",
            "/history",
            "/clear",
            "/chat",
            "/unknown",
        ]:
            await api.post(command)
        assert await store.list(caller.id) == []
        assert len(agent.model.calls) == 0
        await dispatch("/help")
        assert responses[-1].startswith("/chat <question>")
        await dispatch("/chat " + "x" * 8001)
        assert responses[-1].startswith("Usage:")
        await dispatch("Explain Python")
        assert responses[-1] == "bot answer"
        await dispatch("/unknown ignored")
        assert responses[-1] == "Command unavailable; use /help"
        from conversation_support.entrance import addressed_message

        group = parse_callback(
            {
                "msgtype": "text",
                "text": {"content": "ordinary group text"},
                "conversationId": "g",
                "msgId": "m",
                "senderStaffId": "u",
                "conversationType": "2",
                "isInAtList": False,
            },
            connection_id="bot",
        )
        assert not await addressed_message(channel, group)

        first = data_of(await api.post("/new First"))["session"]
        sid = first["id"]
        other_window = str(uuid4())
        await command_error(
            api.post("/session", conversation_id=other_window), 409
        )
        before = len(await store.list(caller.id))
        await api.post("/sessions 1 0", conversation_id=other_window)
        assert len(await store.list(caller.id)) == before
        second = data_of(
            await api.post("/new Second", conversation_id=other_window)
        )["session"]
        assert data_of(await api.post("/session"))["session_id"] == sid
        assert data_of(await api.post("/history"))["messages"] == []
        assert text_of(await api.post("Remember amber")) == "remembered"
        assert text_of(await api.post("/chat What color?")) == "continued"
        assert "Remember amber" in json.dumps(agent.model.calls[2].input)
        assert (
            text_of(await api.post("Separate", conversation_id=other_window))
            == "separate"
        )
        assert "Remember amber" not in json.dumps(agent.model.calls[3].input)

        for command in ["/help", "/session", "/history", "/sessions 1 0"]:
            await api.post(command)
        assert len(agent.model.calls) == 4
        await command_error(api.post("/sessions 0"), 422)
        await command_error(api.post("/session extra"), 422)
        await command_error(api.post("/use " + sid, user="other"), 404)
        await command_error(api.post("/delete " + sid, user="other"), 404)
        await command_error(api.post("/session", user="other"), 409)
        assert (
            data_of(await api.post("/sessions", user="other"))["sessions"] == []
        )
        await api.post("/use " + sid, conversation_id=other_window)
        assert (
            data_of(await api.post("/session", conversation_id=other_window))[
                "session_id"
            ]
            == sid
        )

        command = handlers.entrance.commands[COMMAND_INDEX_CLEAR]
        original_permission = command.permission
        command.permission = Permission(lambda: False)
        try:
            assert (
                text_of(await api.post("/clear"))
                == "Command unavailable; use /help"
            )
        finally:
            command.permission = original_permission
        assert (
            text_of(await api.post("!unknown"))
            == "Command unavailable; use /help"
        )
        assert len(agent.model.calls) == 4

        async with store.hold(caller.id, sid):
            await command_error(api.post("blocked"), 409)
            await command_error(api.post("/clear"), 409)
            await command_error(api.post("/delete " + sid), 409)
        await api.post("/clear")
        assert data_of(await api.post("/history"))["messages"] == []
        assert text_of(await api.post("Fresh")) == "after clear"
        assert "Remember amber" not in json.dumps(agent.model.calls[4].input)
        events = await api.post("Stream this", stream=True)
        assert [kind for kind, _ in events] == [
            "message",
            "message_start",
            "message_delta",
            "message_end",
            "done",
        ]
        assert events[2][1]["delta"] == "stream answer"
        assert events[2][1]["sequence"] == 1
        assert events[1][1]["message_id"] == events[3][1]["message_id"]

        if engine.dialect.name == "postgresql":
            async with store.hold(caller.id, sid):
                try:
                    await ChatConversations(engine).clear(caller.id, sid)
                except ConversationError as error:
                    assert error.status == 409
                else:
                    raise AssertionError(
                        "PostgreSQL must reject concurrent ownership"
                    )
        await engine.dispose()
        lifecycle._conversations = ChatConversations(engine)
        assert len(data_of(await api.post("/history"))["messages"]) == 4
        failure_window = str(uuid4())
        await api.post("/use " + second["id"], conversation_id=failure_window)
        agent.model.extend(
            [ModelStep(error=RuntimeError("private model failure"))]
        )
        failed = await api.post(
            "Fail safely", conversation_id=failure_window, stream=True
        )
        assert failed[-1][0] == "error"
        assert failed[-1][1]["message"] == "internal server error"
        assert not any(kind in {"message_end", "done"} for kind, _ in failed)
        assert "private model failure" not in json.dumps(failed)

        pending = lifecycle._conversations
        entered = asyncio.Event()

        async def cancelled_turn():
            async with pending.hold(caller.id, sid):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(cancelled_turn())
        await entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        async with pending.hold(caller.id, sid):
            pass
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
        await command_error(api.post("/use " + sid), 409)
        await api.post("/delete " + sid)
        await command_error(api.post("/session"), 409)
        await command_error(
            api.post("/session", conversation_id=other_window), 404
        )
        assert await store.history(sid).get_items() == []
        assert (await store.get(caller.id, second["id"])).name == "Second"
        agent.model.assert_complete()
        await chat_connections.disconnect_mcp()
        assert agent.mcp_servers == []

    async def main():
        runtime = execution_lifecycle.ExecutionRuntime(ExecutionSettings())
        execution_lifecycle._runtime = runtime
        await runtime.startup()
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
                await execution_lifecycle.shutdown()

    await main()
    print("conversations and commands verified")


COMMAND_INDEX_CLEAR = 7
