"""Opt-in product acceptance with real HTTP, dispatch, Worker, model, Docker and MCP.

Run only in an isolated database/container with OPSMESH_TEST_PRODUCT=1. The driver
starts the actual API and Worker processes; no execution binding is substituted.
"""

import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from uuid import uuid4

import docker
import httpx
import pytest
from opsmesh_sdk.api.identity import RegisterRequest, create_identity_client
from opsmesh_sdk.api.orchestration import (
    CancelRequest,
    EnvironmentCommandRequest,
    EnvironmentCreateRequest,
    EnvironmentFileDownloadRequest,
    EnvironmentFileUploadRequest,
    ExecutionRequest,
    MCPCreateRequest,
    OperationConfirmation,
    ReconcileRequest,
    SessionCreateRequest,
    create_orchestration_client,
)
from opsmesh_sdk.api.platform import (
    ApprovalDecisionRequest,
    ModelConnectionCreateRequest,
    ModelGrantRequest,
    create_platform_client,
)
from opsmesh_sdk.client import PlatformClient
from sqlalchemy import select
from temporalio.openai_agents import OpenAIAgentsPlugin
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer

from opsmesh.bootstrap.connections import secret_cipher
from opsmesh.bootstrap.temporal import connect_temporal
from opsmesh.config import Settings
from opsmesh.identity.quotas.models import TokenReservation, UserTokenAccount
from opsmesh.identity.users.models import UserProfile
from opsmesh.orchestration.runs.agent_workflow import ExecutionRunWorkflow
from opsmesh.orchestration.runs.models import OperationReceipt
from opsmesh.orchestration.runtime.models import Environment

pytestmark = pytest.mark.skipif(
    os.environ.get("OPSMESH_TEST_PRODUCT") != "1", reason="Full product acceptance opt-in"
)


async def eventually(read, predicate, *, seconds=120):
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        value = await read()
        if predicate(value):
            return value
        assert asyncio.get_running_loop().time() < deadline, f"Timed out: {value!r}"
        await asyncio.sleep(0.2)


@contextmanager
def process(arguments, environment, log_path):
    with log_path.open("w") as log:
        child = subprocess.Popen(arguments, env=environment, stdout=log, stderr=log)
        try:
            yield child
        finally:
            child.terminate()
            try:
                child.wait(20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(10)


def test_real_product_flow(database_sessions, database_url, tmp_path):
    assert os.environ.get("OPSMESH_TEST_MODEL_API_KEY"), "A test model credential is required"
    image = os.environ["OPSMESH_TEST_SANDBOX_IMAGE"]
    model = os.environ.get("OPSMESH_TEST_MODEL_NAME", "gpt-6-luna")
    gateway = os.environ.get("OPSMESH_TEST_MODEL_BASE_URL", "https://dash.ovload.com/v1")
    native_containers = []

    async def verify():
        async with await WorkflowEnvironment.start_local() as temporal:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            queue = f"s7-product-{uuid4()}"
            environment = {
                **os.environ,
                "OPSMESH_DATABASE_URL": database_url,
                "OPSMESH_REDIS_URL": os.environ["OPSMESH_TEST_REDIS_URL"],
                "OPSMESH_TEMPORAL_ADDRESS": temporal.client.service_client.config.target_host,
                "OPSMESH_TEMPORAL_TASK_QUEUE": queue,
                "OPSMESH_STORAGE_ROOT": str(tmp_path / "storage"),
                "OPSMESH_EXECUTION_SANDBOX_IMAGES": json.dumps([image]),
                "OPSMESH_EXECUTION_MODEL_CONTEXT_WINDOWS": json.dumps({model: 32768}),
                "OPSMESH_TRACING_ENABLED": "false",
                "OPSMESH_OTEL_LOGS_ENABLED": "false",
                "OPSMESH_API_RATE_LIMIT_REQUESTS": "10000",
                "OPSMESH_AUTH_RATE_LIMIT_REQUESTS": "10000",
            }
            # The Worker resolves the encrypted administrator key, never this test variable.
            environment.pop("OPSMESH_TEST_MODEL_API_KEY", None)
            worker_args = [sys.executable, "-m", "opsmesh.bootstrap.worker_cli"]
            with (
                process(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "opsmesh.bootstrap.api:create_app",
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--no-access-log",
                    ],
                    environment,
                    tmp_path / "api.log",
                ),
                process(worker_args, environment, tmp_path / "worker.log") as worker,
            ):
                address = f"http://127.0.0.1:{port}"
                async with httpx.AsyncClient() as health:

                    async def ready():
                        try:
                            return (await health.get(address + "/api/v1/health")).status_code
                        except httpx.TransportError:
                            return 0

                    await eventually(ready, lambda code: code != 0, seconds=30)
                async with PlatformClient(address, session_id=str(uuid4())) as client:
                    grant = await create_identity_client(client).auth.register(
                        RegisterRequest(
                            email=f"s7-{uuid4()}@example.com",
                            display_name="S7 acceptance",
                            password="s7-test-only-123",
                        )
                    )
                # Isolated initial administrator provisioning; all product operations use SDK/API.
                with database_sessions() as session, session.begin():
                    session.get(UserProfile, grant.user.user_id).platform_roles = [
                        "admin",
                        "member",
                    ]
                async with PlatformClient(address, session_id=str(grant.session_token)) as client:
                    orchestration = create_orchestration_client(client)
                    platform = create_platform_client(client)
                    connection = await platform.model_connections.create(
                        ModelConnectionCreateRequest(
                            name="Acceptance gateway",
                            api_key=os.environ["OPSMESH_TEST_MODEL_API_KEY"],
                            base_url=gateway,
                            default_model=model,
                            models=[model],
                        ),
                    )
                    await platform.model_connections.grant(
                        connection.connection_id,
                        grant.user.user_id,
                        ModelGrantRequest(models=[model], is_default=True),
                    )
                    env = await orchestration.environments.create(
                        EnvironmentCreateRequest(image=image),
                        idempotency_key="environment",
                    )
                    eid = env.environment_id
                    await eventually(
                        lambda: orchestration.environments.get(eid),
                        lambda item: item.status == "ready",
                    )
                    with database_sessions() as session:
                        state = secret_cipher(Settings()).decrypt_payload(
                            session.get(Environment, eid).encrypted_state
                        )
                        native_containers.append(state["container_id"])

                    async def operation(receipt):
                        return await eventually(
                            lambda: orchestration.environments.operations.get(
                                eid, receipt.operation_id
                            ),
                            lambda item: item.status in {"completed", "uncertain"},
                        )

                    async def command(arguments, key):
                        receipt = await orchestration.environments.commands.execute(
                            eid, EnvironmentCommandRequest(command=arguments), idempotency_key=key
                        )
                        result = await operation(receipt)
                        assert result.status == "completed", result
                        return result.result

                    async def upload(path, content):
                        result = await operation(
                            await orchestration.environments.files.upload(
                                eid,
                                EnvironmentFileUploadRequest(
                                    path=path, content_base64=base64.b64encode(content).decode()
                                ),
                                idempotency_key=f"upload-{path}",
                            )
                        )
                        assert result.status == "completed"

                    await upload("input.txt", b"S7_INPUT")
                    result = await operation(
                        await orchestration.environments.files.download(
                            eid,
                            EnvironmentFileDownloadRequest(path="input.txt"),
                            idempotency_key="download-input",
                        )
                    )
                    assert base64.b64decode(result.result["content_base64"]) == b"S7_INPUT"

                    async def submit(name, prompt, *, tools=(), mcp=()):
                        history = await orchestration.sessions.create(SessionCreateRequest())
                        return await orchestration.runs.create(
                            ExecutionRequest(
                                session_id=history.session_id,
                                input=prompt,
                                entry_agent="main",
                                environment_id=eid,
                                agents={
                                    "main": {
                                        "name": "Acceptance",
                                        "instructions": "Call the requested tool exactly once.",
                                        "model": {
                                            "connection_id": str(connection.connection_id),
                                            "name": model,
                                            "max_output_tokens": 2048,
                                            "reasoning_effort": os.environ.get(
                                                "OPSMESH_TEST_MODEL_REASONING_EFFORT"
                                            ),
                                        },
                                        "tools": list(tools),
                                        "mcp_connections": list(mcp),
                                    }
                                },
                            ),
                            idempotency_key=name,
                        )

                    async def approve(run):
                        inbox = await platform.approvals.list()
                        approvals = [
                            a
                            for a in inbox.approvals
                            if a.target_id.startswith(f"{run.run_id}:") and a.status == "pending"
                        ]
                        assert len(approvals) == 1
                        await platform.approvals.approve(
                            approvals[0].approval_id,
                            ApprovalDecisionRequest(idempotency_key=str(uuid4())),
                        )

                    async def finished(run):
                        result = await eventually(
                            lambda: orchestration.runs.get(run.run_id),
                            lambda item: (
                                item.status
                                in {"succeeded", "failed", "cancelled", "needs_attention"}
                            ),
                        )
                        assert result.status == "succeeded", result
                        return result

                    tool = {
                        "name": "write_marker",
                        "description": "Write the acceptance marker",
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                        "command": [
                            "python3",
                            "-c",
                            "from pathlib import Path; "
                            "p=Path('/workspace/marker.txt'); "
                            "p.open('a').write('once\\n'); print('S7_TOOL_OK')",
                        ],
                    }
                    run = await submit(
                        "sandbox", "Call write_marker once, then answer S7_OK.", tools=[tool]
                    )
                    await eventually(
                        lambda: orchestration.runs.get(run.run_id),
                        lambda item: item.status == "waiting_approval",
                    )
                    worker.kill()
                    worker.wait(10)
                    with process(
                        worker_args, environment, tmp_path / "worker-restarted.log"
                    ) as restarted_worker:
                        await approve(run)
                        result = await finished(run)
                        assert "S7_OK" in str(result.output)
                        assert (await command(["cat", "/workspace/marker.txt"], "marker"))[
                            "stdout"
                        ] == "once\n"
                        events = await orchestration.runs.events.list(run.run_id, limit=1)
                        later = await orchestration.runs.events.list(
                            run.run_id, after=events.next_cursor
                        )
                        assert later.events and all(
                            e.sequence > events.next_cursor for e in later.events
                        )
                        history = await orchestration.sessions.history.list(run.session_id)
                        assert len(history.items) >= 4
                        settings = Settings(
                            temporal_address=environment["OPSMESH_TEMPORAL_ADDRESS"]
                        )
                        replay_client = await connect_temporal(settings)
                        with database_sessions() as session:
                            workflow_id = f"run:{run.run_id}"
                            before = len(session.scalars(select(TokenReservation)).all())
                        workflow_history = await replay_client.get_workflow_handle(
                            workflow_id
                        ).fetch_history()
                        await Replayer(
                            workflows=[ExecutionRunWorkflow],
                            plugins=[OpenAIAgentsPlugin()],
                            data_converter=replay_client.data_converter,
                        ).replay_workflow(workflow_history)
                        with database_sessions() as session:
                            assert len(session.scalars(select(TokenReservation)).all()) == before
                        assert (await command(["cat", "/workspace/marker.txt"], "marker-replayed"))[
                            "stdout"
                        ] == "once\n"
                        print(
                            "PRODUCT sandbox: approval/restart/replay; "
                            "one effect; environment retained"
                        )

                        cancel_tool = {
                            **tool,
                            "name": "cancel_marker",
                            "approval_required": False,
                            "command": [
                                "python3",
                                "-c",
                                "import time; from pathlib import Path; "
                                "Path('/workspace/cancel-started').write_text('yes'); "
                                "time.sleep(5); "
                                "Path('/workspace/cancel-finished').write_text('once'); "
                                "print('done')",
                            ],
                        }
                        cancelling = await submit(
                            "cancel", "Call cancel_marker once.", tools=[cancel_tool]
                        )

                        async def tool_started():
                            return await orchestration.runs.operations(cancelling.run_id)

                        await eventually(
                            tool_started,
                            lambda items: any(
                                item.kind == "tool" and item.status == "started" for item in items
                            ),
                        )
                        current = await orchestration.runs.get(cancelling.run_id)
                        await orchestration.runs.cancel(
                            cancelling.run_id,
                            CancelRequest(command_id=uuid4(), expected_revision=current.revision),
                        )
                        cancelled = await eventually(
                            lambda: orchestration.runs.get(cancelling.run_id),
                            lambda item: item.status in {"cancelled", "needs_attention", "failed"},
                        )
                        assert cancelled.status == "cancelled", cancelled
                        assert (
                            await command(["cat", "/workspace/cancel-finished"], "cancel-marker")
                        )["stdout"] == "once"
                        assert (await orchestration.environments.get(eid)).status == "ready"
                        print("PRODUCT cancellation: command settled, environment retained")

                        mcp_program = (
                            "from mcp.server.mcpserver import MCPServer\n"
                            "from pathlib import Path\n"
                            "m=MCPServer('S7')\n@m.tool()\n"
                            "def s7_echo(text: str) -> str:\n"
                            " p=Path('/workspace/mcp-marker'); p.open('a').write('once\\n')\n"
                            " return 'S7_MCP_OK '+text\n"
                            "m.run(transport='stdio')\n"
                        )
                        await upload("mcp_server.py", mcp_program.encode())
                        for transport, endpoint in [
                            ("stdio", None),
                            ("streamable_http", "https://learn.microsoft.com/api/mcp"),
                        ]:
                            name = "acceptance-" + transport
                            mcp = await orchestration.mcp_connections.create(
                                MCPCreateRequest(
                                    name=name,
                                    transport=transport,
                                    endpoint=endpoint,
                                    environment_id=eid if transport == "stdio" else None,
                                    command=["python3", "/workspace/mcp_server.py"]
                                    if transport == "stdio"
                                    else [],
                                ),
                                idempotency_key=name,
                            )
                            ready = await eventually(
                                lambda mcp=mcp: orchestration.mcp_connections.get(
                                    mcp.connection_id
                                ),
                                lambda item: item.status in {"ready", "failed"},
                            )
                            assert ready.status == "ready", ready
                            manifest = await orchestration.mcp_connections.tools.list(
                                mcp.connection_id
                            )
                            tool_name = (
                                "s7_echo" if transport == "stdio" else "microsoft_docs_search"
                            )
                            assert any(t["name"] == tool_name for t in manifest.tools)
                            bound = {
                                "connection_id": str(mcp.connection_id),
                                "tools_digest": manifest.tools_digest,
                                "allowed_tools": [tool_name],
                            }
                            prompt = (
                                "Call s7_echo once with text hello, then answer S7_MCP_OK."
                                if transport == "stdio"
                                else "Call microsoft_docs_search once for Azure Blob Storage. "
                                "Then answer S7_REMOTE_OK."
                            )
                            mcp_run = await submit(name, prompt, mcp=[bound])
                            await eventually(
                                lambda mcp_run=mcp_run: orchestration.runs.get(mcp_run.run_id),
                                lambda item: item.status == "waiting_approval",
                            )
                            await approve(mcp_run)
                            await finished(mcp_run)
                            receipts = await orchestration.runs.operations(mcp_run.run_id)
                            assert sum(r.kind == "mcp" and r.accounted for r in receipts) == 1
                            await orchestration.mcp_connections.close(
                                mcp.connection_id, idempotency_key=name + "-close"
                            )
                            await eventually(
                                lambda mcp=mcp: orchestration.mcp_connections.get(
                                    mcp.connection_id
                                ),
                                lambda item: item.status == "closed",
                            )
                            print(
                                f"PRODUCT {transport} MCP: persisted connection, model call, close"
                            )
                        assert (await command(["cat", "/workspace/mcp-marker"], "mcp-marker"))[
                            "stdout"
                        ] == "once\n"
                        crash_tool = {
                            **cancel_tool,
                            "name": "crash_marker",
                            "command": [
                                "python3",
                                "-c",
                                "import time; from pathlib import Path; "
                                "Path('/workspace/crash-marker').open('a').write('once\\n'); "
                                "time.sleep(5); print('done')",
                            ],
                        }
                        interrupted = await submit(
                            "crash", "Call crash_marker once.", tools=[crash_tool]
                        )
                        await eventually(
                            lambda: orchestration.runs.operations(interrupted.run_id),
                            lambda items: any(
                                item.kind == "tool" and item.status == "started" for item in items
                            ),
                        )
                        # Kill during external I/O, then attest the actual filesystem effect.
                        restarted_worker.kill()
                        restarted_worker.wait(10)
                        await asyncio.sleep(6)
                        native = docker.from_env()
                        try:
                            effect = native.containers.get(native_containers[0]).exec_run(
                                ["cat", "/workspace/crash-marker"]
                            )
                            assert effect.exit_code == 0 and effect.output == b"once\n"
                        finally:
                            native.close()
                        with process(worker_args, environment, tmp_path / "worker-recovered.log"):
                            uncertain = await eventually(
                                lambda: orchestration.runs.get(interrupted.run_id),
                                lambda item: item.status == "needs_attention",
                            )
                            unresolved = [
                                item
                                for item in await orchestration.runs.operations(interrupted.run_id)
                                if item.status in {"started", "uncertain"}
                            ]
                            assert len(unresolved) == 1 and unresolved[0].kind == "tool"
                            reconciled = await orchestration.runs.reconcile(
                                interrupted.run_id,
                                ReconcileRequest(
                                    command_id=uuid4(),
                                    expected_revision=uncertain.revision,
                                    evidence={
                                        "marker": "once",
                                        "source": "isolated Docker filesystem",
                                    },
                                    operations=[
                                        OperationConfirmation(
                                            operation_id=unresolved[0].operation_id,
                                            outcome="succeeded",
                                            usage={"tool_invocations": 1},
                                            result={"stdout": "done", "exit_code": 0},
                                        )
                                    ],
                                ),
                            )
                            assert reconciled.status == "failed"
                            assert (
                                await orchestration.sessions.get(interrupted.session_id)
                            ).status == "closed"
                            assert (await orchestration.environments.get(eid)).status == "ready"
                            assert (
                                await command(["cat", "/workspace/crash-marker"], "crash-marker")
                            )["stdout"] == "once\n"
                            print(
                                "PRODUCT crash: unknown effect held; "
                                "attested reconciliation; no rerun"
                            )
                            await orchestration.environments.close(
                                eid, idempotency_key="close-environment"
                            )
                            await eventually(
                                lambda: orchestration.environments.get(eid),
                                lambda item: item.status == "closed",
                            )
                        with database_sessions() as session:
                            account = session.get(UserTokenAccount, grant.user.user_id)
                            assert account.reserved_tokens == 0 and account.used_tokens > 0
                            receipts = session.scalars(select(OperationReceipt)).all()
                            assert all(r.accounted and r.status == "completed" for r in receipts)
                        print("PRODUCT accounting: effects settled; explicit environment close")

    try:
        asyncio.run(verify())
    finally:
        client = docker.from_env()
        try:
            for container_id in native_containers:
                try:
                    remaining = client.containers.get(container_id)
                except docker.errors.NotFound:
                    continue
                remaining.remove(force=True)
        finally:
            client.close()
