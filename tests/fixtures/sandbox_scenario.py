"""Exercise native sandbox history and workspace lifetimes."""

import asyncio
import json
import os
import runpy
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import anyio
from agents import Runner
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
from example_sandbox import execution, lifecycle
from example_sandbox.agent import agent
from example_sandbox.sessions import SandboxConversations
from example_sandbox.workspace import SandboxWorkspaces
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.requests import Request

from opsmesh.drivers import base as driver_state
from opsmesh.plugins.channels import UserContext
from opsmesh.plugins.channels.api import (
    APIChannel,
    APIRouter,
    HTTPError,
    Settings,
)
from opsmesh.plugins.channels.api.adapter import APIAdapter
from opsmesh.plugins.filesystem import files
from opsmesh.plugins.model import PluginStatus


class ScriptedClient:
    """Use native SDK scripted sessions while keeping files on test disk."""

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
    """Test multi-turn history, commands, workspace persistence and cleanup."""
    plugin.status = PluginStatus.READY
    caller = UserContext(id="sandbox-user")
    api = APIChannel(
        Settings(identities=[{"id": "test", "token_ref": "TOKEN"}]),
        APIRouter(),
    )
    api.adapter = APIAdapter(driver_state.get_driver())
    model = ScriptedModel(
        [
            ModelStep(output=[assistant_message(text)])
            for text in ["first", "second", "other"]
        ]
    )
    agent.model = model
    original_capabilities = agent.capabilities
    agent.capabilities = []

    async def run_native(selected_agent, message, **options):
        options.pop("trace_context")
        options.pop("plugin_id")
        options["run_config"].tracing_disabled = True
        return await Runner.run(selected_agent, message, **options)

    execution.run = run_native

    async def invoke(method, path, data=None, session_id=None, owner=None):
        selected = next(
            item
            for item in routes
            if item.method == method and item.relative_path == path
        )
        delivered = False

        async def receive():
            nonlocal delivered
            if delivered:
                await anyio.sleep_forever()
            delivered = True
            return {
                "type": "http.request",
                "more_body": False,
                "body": json.dumps(data).encode() if data is not None else b"",
            }

        request = Request(
            {
                "type": "http",
                "method": method,
                "path": selected.path,
                "headers": [],
                "query_string": b"",
                "path_params": {"id": session_id} if session_id else {},
            },
            receive,
        )
        result = await api._invoke(
            selected, request, SimpleNamespace(caller=owner or caller)
        )
        return json.loads(result.body) if result.status_code != 204 else None

    async def denied(operation, status):
        try:
            await operation
        except HTTPError as error:
            assert error.status_code == status
        else:
            raise AssertionError("Expected HTTP rejection")

    async def verify(engine, backend):
        async with engine.begin() as connection:
            await connection.run_sync(migrate)
        workspace = SandboxWorkspaces(backend)
        store = SandboxConversations(engine, workspace)
        lifecycle._conversations = store
        first = await invoke("POST", "/sessions", {"name": "First"})
        sid = first["id"]
        second = await invoke("POST", "/sessions", {"name": "Second"})
        second_id = second["id"]
        result = await invoke(
            "POST", "/chat", {"session_id": sid, "message": "Remember amber"}
        )
        assert result == {"text": "first", "session_id": sid}
        state_file = workspace.state_file(sid)
        saved = json.loads(state_file.read_text())
        note = workspace.directory(sid) / "note.txt"
        note.write_text("persistent workspace", encoding="utf-8")
        await invoke(
            "POST", "/chat", {"session_id": sid, "message": "Continue"}
        )
        assert note.read_text() == "persistent workspace"
        assert (
            json.loads(state_file.read_text())["session_id"]
            == saved["session_id"]
        )
        assert "Remember amber" in json.dumps(model.calls[1].input)
        await invoke(
            "POST", "/chat", {"session_id": second_id, "message": "Different"}
        )
        assert not (workspace.directory(second_id) / "note.txt").exists()
        assert "Remember amber" not in json.dumps(model.calls[2].input)

        for command in ["/help", "/session", "/history", "/clear"]:
            await invoke(
                "POST", "/chat", {"session_id": sid, "message": command}
            )
        assert len(model.calls) == 3
        assert note.exists()
        assert (
            await invoke("GET", "/sessions/{id}/messages", session_id=sid) == []
        )
        await denied(
            invoke(
                "DELETE",
                "/sessions/{id}/workspace",
                session_id=sid,
                owner=UserContext(id="other"),
            ),
            404,
        )
        assert note.exists()
        async with store.hold(caller.id, sid):
            await denied(
                invoke("DELETE", "/sessions/{id}/workspace", session_id=sid),
                409,
            )
            await denied(
                invoke("DELETE", "/sessions/{id}", session_id=sid), 409
            )

        if engine.dialect.name == "postgresql":
            competing = SandboxConversations(engine, workspace)
            async with store.hold(caller.id, sid):
                try:
                    await competing.clear(caller.id, sid)
                except Exception as error:
                    assert getattr(error, "status", None) == 409
                else:
                    raise AssertionError(
                        "PostgreSQL must reject concurrent ownership"
                    )

        await engine.dispose()
        lifecycle._conversations = SandboxConversations(
            engine, SandboxWorkspaces(backend)
        )
        async with workspace.open(sid) as sandbox:
            assert note.exists()
            if sys.platform != "win32":
                result = await sandbox.exec("printf native-linux-check")
                assert "native-linux-check" in str(result.stdout)
        await invoke(
            "POST", "/chat", {"session_id": sid, "message": "/reset_workspace"}
        )
        assert not note.exists()
        assert not state_file.exists()
        assert (await invoke("GET", "/sessions/{id}", session_id=sid))[
            "id"
        ] == sid

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
        await invoke("DELETE", "/sessions/{id}", session_id=sid)
        assert not workspace.directory(sid).exists()
        assert not workspace.state_file(sid).exists()
        await denied(invoke("GET", "/sessions/{id}", session_id=sid), 404)
        assert (workspace.directory(second_id)).exists()
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
            result = await invoke(
                "POST",
                "/chat",
                {"session_id": second_id, "message": "Write a file"},
            )
            assert result["text"] == "tool finished"
            assert (
                workspace.directory(second_id) / "tool-proof.txt"
            ).read_text().strip() == "native-tool"
        await invoke("DELETE", "/sessions/{id}", session_id=second_id)
        model.assert_complete()

    async def main():
        with tempfile.TemporaryDirectory() as temporary:
            from opsmesh.plugins import get_plugin

            storage = get_plugin("filesystem")
            storage.config_values = {"root": temporary + "/files"}
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

    asyncio.run(main())
    print("sandbox conversations verified")
