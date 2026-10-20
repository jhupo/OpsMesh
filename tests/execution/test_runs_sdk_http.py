"""Generated SDK over a real HTTP listener and PostgreSQL; Redis alone is substituted."""

import asyncio
import base64
import socket
import sys
import threading
import time
from uuid import uuid4

import pytest
import uvicorn
from opsmesh_sdk.api.identity import create_identity_client
from opsmesh_sdk.api.orchestration import (
    CancelRequest,
    EnvironmentCommandRequest,
    EnvironmentCreateRequest,
    EnvironmentFileDownloadRequest,
    EnvironmentFileUploadRequest,
    ExecutionRequest,
    MCPCreateRequest,
    RuntimeControlRequest,
    SessionCreateRequest,
    create_orchestration_client,
)
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.errors import ConflictError, NotFoundError
from sqlalchemy import select

import opsmesh.bootstrap.api as api_module
from opsmesh.orchestration.runs.activities import RunActivities
from opsmesh.orchestration.runs.contracts import RunCompletion
from opsmesh.orchestration.runtime.activities import RuntimeActivities
from opsmesh.orchestration.runtime.contracts import RuntimeCommand
from opsmesh.orchestration.runtime.models import EnvironmentOperationReceipt
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry
from tests.conftest import build_test_settings
from tests.execution.test_session_routes import fake_api_resources, register_account


@pytest.fixture
def http_server(database_url, database_sessions, monkeypatch):
    monkeypatch.setattr(api_module, "build_api_resources", fake_api_resources)
    app = api_module.create_app(build_test_settings(database_url))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))

    def serve():
        options = {"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {}
        with asyncio.Runner(**options) as runner:
            runner.run(server.serve(sockets=[listener]))

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "The actual API listener did not start"
        yield address, app
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        assert not thread.is_alive(), "The API listener did not shut down"


def test_sdk_submission_queries_cancellation_and_event_cursor(http_server, database_sessions):
    address, app = http_server
    token = register_account(database_sessions, email="sdk-s7@example.com")

    async def exercise():
        async with PlatformClient(address, session_id=str(token)) as client:
            execution = create_orchestration_client(client)
            session = await execution.sessions.create(SessionCreateRequest())
            payload = ExecutionRequest(
                session_id=session.session_id,
                input="hello",
                entry_agent="main",
                agents={
                    "main": {
                        "name": "Main",
                        "instructions": "Answer",
                        "model": {
                            "connection_id": str(uuid4()),
                            "name": "configured-later",
                            "max_output_tokens": 10,
                        },
                    }
                },
            )
            created = await execution.runs.create(payload, idempotency_key="sdk-create")
            repeated = await execution.runs.create(payload, idempotency_key="sdk-create")
            assert repeated.run_id == created.run_id and created.status == "queued"
            assert created.sequence == 1
            assert (await execution.runs.get(created.run_id)).revision == created.revision
            assert (
                len(await execution.runs.list(session_id=session.session_id, after_sequence=0)) == 1
            )
            assert (
                await execution.runs.list(
                    session_id=session.session_id, after_sequence=created.sequence
                )
                == []
            )
            assert await execution.runs.operations(created.run_id) == []
            page = await execution.runs.events.list(created.run_id, limit=1)
            assert [event.kind for event in page.events] == ["queued"]
            request = CancelRequest(command_id=uuid4(), expected_revision=created.revision)
            cancelled = await execution.runs.cancel(created.run_id, request)
            assert cancelled.status == "cancelling"
            assert (
                await execution.runs.cancel(created.run_id, request)
            ).revision == cancelled.revision
            activities = RunActivities(
                database_sessions,
                app.state.execution_scope_admission,
                app.state.execution_bindings,
                app.state.secret_cipher,
            )
            await activities.finish(RunCompletion(run_id=created.run_id, status="cancelled"))
            events = [
                event
                async for event in execution.runs.events.stream(
                    created.run_id, after=page.next_cursor
                )
            ]
            assert [event.kind for event in events] == ["cancelling", "cancelled"]
            assert all(event.sequence > page.next_cursor for event in events)
            assert [
                event
                async for event in execution.runs.events.stream(
                    created.run_id, after=events[-1].sequence
                )
            ] == []

    asyncio.run(exercise())


def test_live_event_stream_stops_after_login_revocation(http_server, database_sessions):
    address, _ = http_server
    token = register_account(database_sessions, email="sdk-stream-revoked@example.com")

    async def exercise():
        async with PlatformClient(address, session_id=str(token)) as client:
            execution = create_orchestration_client(client)
            session = await execution.sessions.create(SessionCreateRequest())
            run = await execution.runs.create(
                ExecutionRequest(
                    session_id=session.session_id,
                    input="hello",
                    entry_agent="main",
                    agents={
                        "main": {
                            "name": "Main",
                            "model": {"connection_id": str(uuid4()), "name": "test"},
                        }
                    },
                ),
                idempotency_key="stream-revoke",
            )
            stream = execution.runs.events.stream(run.run_id)
            first = await anext(stream)
            assert first.kind == "queued"
            await create_identity_client(client).auth.logout()
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(anext(stream), timeout=5)

    asyncio.run(exercise())


def test_sdk_environment_files_commands_and_lifecycle(http_server, database_sessions):
    address, app = http_server
    token = register_account(database_sessions, email="sdk-runtime@example.com")
    app.state.execution_sandbox_images = ["approved"]

    class Backend:
        def __init__(self):
            self.files = {}
            self.calls = []

        async def create(self, **kwargs):
            return {"native_id": "deterministic-http-test"}

        async def resume(self, state):
            return state

        def state(self, native):
            return native

        async def execute(self, native, action, arguments):
            self.calls.append(action)
            if action == "write":
                content = base64.b64decode(arguments["content_base64"])
                self.files[arguments["path"]] = content
                return {"bytes_written": len(content)}
            if action == "read":
                return {"content_base64": base64.b64encode(self.files[arguments["path"]]).decode()}
            if action == "command":
                assert arguments["command"] == ["printf", "hello"]
                return {"stdout": "hello", "stderr": "", "exit_code": 0}
            return {}

    backend = Backend()
    activities = RuntimeActivities(
        database_sessions,
        app.state.execution_scope_admission,
        app.state.execution_bindings,
        app.state.secret_cipher,
        backend,
        app.state.execution_storage,
    )

    async def execute_staged(environment_id, key):
        with database_sessions() as session:
            receipt = session.scalar(
                select(EnvironmentOperationReceipt).where(
                    EnvironmentOperationReceipt.environment_id == environment_id,
                    EnvironmentOperationReceipt.operation_id == key,
                )
            )
            entry = session.scalars(
                select(TemporalOutboxEntry).where(
                    TemporalOutboxEntry.workflow_id == receipt.workflow_id,
                    TemporalOutboxEntry.operation == "start",
                )
            ).one()
            payload = app.state.secret_cipher.decrypt_payload(entry.sealed_payload)
        return await activities.execute(RuntimeCommand.model_validate(payload["execution"]))

    async def exercise():
        async with PlatformClient(address, session_id=str(token)) as client:
            envs = create_orchestration_client(client).environments
            env = await envs.create(
                EnvironmentCreateRequest(image="approved"), idempotency_key="env"
            )
            await execute_staged(env.environment_id, "env")
            assert (await envs.get(env.environment_id)).status == "ready"
            upload = EnvironmentFileUploadRequest(path="input.txt", content_base64="aGVsbG8=")
            first = await envs.files.upload(env.environment_id, upload, idempotency_key="upload")
            repeated = await envs.files.upload(env.environment_id, upload, idempotency_key="upload")
            assert first.operation_id == repeated.operation_id
            with pytest.raises(ConflictError):
                await envs.files.upload(
                    env.environment_id,
                    EnvironmentFileUploadRequest(path="other.txt", content_base64="aA=="),
                    idempotency_key="upload",
                )
            await execute_staged(env.environment_id, "upload")
            await execute_staged(env.environment_id, "upload")
            assert backend.calls.count("write") == 1
            completed = await envs.files.upload(
                env.environment_id, upload, idempotency_key="upload"
            )
            assert completed.status == "completed" and completed.revision == 2
            await envs.files.download(
                env.environment_id,
                EnvironmentFileDownloadRequest(path="input.txt"),
                idempotency_key="download",
            )
            await execute_staged(env.environment_id, "download")
            view = await envs.operations.get(env.environment_id, "download")
            assert view.result == {"content_base64": "aGVsbG8="} and view.revision == 2
            await envs.commands.execute(
                env.environment_id,
                EnvironmentCommandRequest(command=["printf", "hello"]),
                idempotency_key="command",
            )
            await execute_staged(env.environment_id, "command")
            assert (await envs.operations.get(env.environment_id, "command")).result[
                "stdout"
            ] == "hello"
            await envs.resume(env.environment_id, idempotency_key="resume")
            await execute_staged(env.environment_id, "resume")
            await envs.close(env.environment_id, idempotency_key="close")
            await execute_staged(env.environment_id, "close")
            assert (await envs.get(env.environment_id)).status == "closed"

    asyncio.run(exercise())


def test_sdk_mcp_listing_tools_and_pending_cancellation(http_server, database_sessions):
    address, _ = http_server
    token = register_account(database_sessions, email="sdk-mcp@example.com")
    other = register_account(database_sessions, email="sdk-mcp-other@example.com")

    async def exercise():
        async with PlatformClient(address, session_id=str(token)) as client:
            connections = create_orchestration_client(client).mcp_connections
            item = await connections.create(
                MCPCreateRequest(
                    name="sdk-mcp", transport="streamable_http", endpoint="https://mcp.example.com"
                ),
                idempotency_key="mcp-create",
            )
            assert [row.connection_id for row in await connections.list()] == [item.connection_id]
            assert await connections.list(after=item.connection_id) == []
            tools = await connections.tools.list(item.connection_id)
            assert tools.tools == [] and tools.tools_digest == item.tools_digest
            await connections.operations.cancel(
                item.connection_id,
                "mcp-create",
                RuntimeControlRequest(command_id=uuid4(), expected_revision=0),
            )
            assert (
                await connections.operations.get(item.connection_id, "mcp-create")
            ).revision == 1
            resumed = await connections.connect(item.connection_id, idempotency_key="connect")
            assert resumed.status == "pending"
            await connections.operations.cancel(
                item.connection_id,
                "connect",
                RuntimeControlRequest(command_id=uuid4(), expected_revision=resumed.revision),
            )
            closed = await connections.close(item.connection_id, idempotency_key="close")
            assert closed.status == "pending"
            async with PlatformClient(address, session_id=str(other)) as other_client:
                hidden = create_orchestration_client(other_client).mcp_connections
                assert await hidden.list() == []
                with pytest.raises(NotFoundError):
                    await hidden.get(item.connection_id)

    asyncio.run(exercise())
