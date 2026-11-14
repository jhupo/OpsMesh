"""Verify native sandbox streams retain owned history and workspace scopes."""

import asyncio
import json
import os
import runpy
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from agents.sandbox.session.sandbox_session_state import SandboxSessionState
from agents.testing import (
    ModelStep,
    ScriptedModel,
    assistant_message,
    function_call,
    scripted_sandbox_session,
)
from alembic.migration import MigrationContext
from alembic.operations import Operations
from example_sandbox import lifecycle
from example_sandbox.agent import agent
from example_sandbox.sessions import SandboxConversations
from example_sandbox.workspace import SandboxWorkspaces
from sqlalchemy.ext.asyncio import create_async_engine

from opsmesh.plugins.channels import UserContext
from opsmesh.plugins.execution import Settings as ExecutionSettings
from opsmesh.plugins.execution import _lifecycle as execution_lifecycle
from opsmesh.plugins.filesystem import files
from opsmesh.plugins.model import PluginStatus

harness = runpy.run_path("tests/fixtures/chat_scenario.py")
MessageHarness = harness["MessageHarness"]
data_of = harness["data_of"]
text_of = harness["text_of"]
command_error = harness["command_error"]


class ScriptedClient:
    def __init__(self):
        self.sessions = []

    async def create(self, *, manifest):
        Path(manifest.root).mkdir(parents=True, exist_ok=True)
        session = scripted_sandbox_session([], manifest=manifest)
        self.sessions.append(session)
        return session

    async def resume(self, state):
        session = scripted_sandbox_session([], manifest=state.manifest)
        session.state = state
        self.sessions.append(session)
        return session

    def serialize_session_state(self, state):
        return state.model_dump(mode="json")

    def deserialize_session_state(self, payload):
        return SandboxSessionState.model_validate(payload)


def migrate(connection):
    namespace = runpy.run_path(
        "examples/sandbox/example_sandbox/migrations/0001_example_sandbox.py"
    )
    with Operations.context(MigrationContext.configure(connection)):
        namespace["upgrade"]()


def run_scenario(plugin, routes):
    from opsmesh.plugins import get_loaded_plugins

    for loaded in get_loaded_plugins():
        loaded.status = PluginStatus.READY
    caller = UserContext(id="sandbox-user")
    api = MessageHarness(routes[0], caller)
    model = ScriptedModel(
        [
            ModelStep(output=[assistant_message(text)])
            for text in ["first", "second", "other"]
        ]
    )
    agent.model = model
    original_capabilities = agent.capabilities
    agent.capabilities = []

    async def verify(engine, backend):
        async with engine.begin() as connection:
            await connection.run_sync(migrate)
        workspace = SandboxWorkspaces(backend)
        store = SandboxConversations(engine, workspace)
        lifecycle._conversations = store
        for command in [
            "/help",
            "/sessions",
            "/session",
            "/history",
            "/clear",
            "/reset_workspace",
            "/unknown",
        ]:
            await api.post(command)
        assert await store.list(caller.id) == []
        assert len(model.calls) == 0
        sid = data_of(await api.post("/new First"))["session_id"]
        other_window = str(uuid4())
        second_id = data_of(
            await api.post("/new Second", conversation_id=other_window)
        )["session_id"]
        assert text_of(await api.post("Remember amber")) == "first"
        state_file = workspace.state_file(sid)
        saved = json.loads(state_file.read_text())
        note = workspace.directory(sid) / "note.txt"
        note.write_text("persistent workspace", encoding="utf-8")
        events = await api.post("Continue", stream=True)
        assert events[-1][0] == "done"
        assert events[-2][0] == "message_end"
        assert note.read_text() == "persistent workspace"
        assert (
            json.loads(state_file.read_text())["session_id"]
            == saved["session_id"]
        )
        assert "Remember amber" in json.dumps(model.calls[1].input)
        assert (
            text_of(await api.post("Different", conversation_id=other_window))
            == "other"
        )
        assert not (workspace.directory(second_id) / "note.txt").exists()
        assert "Remember amber" not in json.dumps(model.calls[2].input)
        for command in ["/help", "/session", "/history", "/clear"]:
            await api.post(command)
        assert len(model.calls) == 3
        assert note.exists()
        assert data_of(await api.post("/history"))["messages"] == []
        await command_error(
            api.post("/use " + sid, owner=UserContext(id="other")), 404
        )
        await command_error(
            api.post("/reset_workspace", owner=UserContext(id="other")), 409
        )
        assert note.exists()
        async with store.hold(caller.id, sid):
            await command_error(api.post("/reset_workspace"), 409)
            await command_error(api.post("/delete " + sid), 409)
        await engine.dispose()
        lifecycle._conversations = SandboxConversations(
            engine, SandboxWorkspaces(backend)
        )
        async with workspace.open(sid) as sandbox:
            assert note.exists()
            if sys.platform != "win32":
                result = await sandbox.exec("printf native-linux-check")
                assert "native-linux-check" in str(result.stdout)
        await api.post("/reset_workspace")
        assert not note.exists()
        assert not state_file.exists()
        assert data_of(await api.post("/session"))["session_id"] == sid

        entered = asyncio.Event()

        async def active_workspace():
            async with workspace.open(sid):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(active_workspace())
        await entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        async with workspace.open(sid):
            pass
        if isinstance(backend, ScriptedClient):
            assert all(
                [not await session.running() for session in backend.sessions]
            )
        await api.post("/delete " + sid)
        assert not workspace.directory(sid).exists()
        assert not workspace.state_file(sid).exists()
        if sys.platform != "win32":
            agent.capabilities = original_capabilities
            model.extend(
                [
                    ModelStep(
                        output=[
                            function_call(
                                "exec_command",
                                {
                                    "cmd": "echo native-tool > tool-proof.txt",
                                    "login": False,
                                },
                                call_id="shell-proof",
                            )
                        ]
                    ),
                    ModelStep(output=[assistant_message("tool finished")]),
                ]
            )
            assert (
                text_of(
                    await api.post("Write a file", conversation_id=other_window)
                )
                == "tool finished"
            )
            assert (
                workspace.directory(second_id) / "tool-proof.txt"
            ).read_text().strip() == "native-tool"
        await api.post("/delete " + second_id, conversation_id=other_window)
        model.assert_complete()

    async def main():
        from opsmesh.plugins import get_plugin

        runtime = execution_lifecycle.ExecutionRuntime(ExecutionSettings())
        execution_lifecycle._runtime = runtime
        await runtime.startup()
        with tempfile.TemporaryDirectory() as temporary:
            get_plugin("filesystem").config_values = {
                "root": temporary + "/files"
            }
            await files._startup()
            engine = create_async_engine(
                os.environ.get("OPSMESH_TEST_DATABASE_URL")
                or "sqlite+aiosqlite:///"
                + str(Path(temporary) / "sandbox.sqlite")
            )
            if sys.platform == "win32":
                backend = ScriptedClient()
            else:
                from agents.sandbox.sandboxes.unix_local import (
                    UnixLocalSandboxClient,
                )

                backend = UnixLocalSandboxClient(inherit_host_environment=False)
            try:
                await verify(engine, backend)
            finally:
                lifecycle._conversations = None
                await engine.dispose()
                await files._shutdown()
                await execution_lifecycle.shutdown()

    asyncio.run(main())
    print("sandbox conversations verified")
