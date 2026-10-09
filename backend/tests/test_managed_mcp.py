from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.app.capabilities.mcp.execution.contracts import McpExecutionError
from backend.app.capabilities.mcp.managed_jobs import ManagedMcpJobHandler
from backend.app.capabilities.mcp.managed_maintenance import reconcile_managed_mcp
from backend.app.capabilities.mcp.managed_runtime import ManagedMcpToolAdapter
from backend.app.capabilities.mcp.managed_schemas import ManagedMcpCreateRequest, ManagedMcpResponse
from backend.app.capabilities.mcp.managed_service import ManagedMcpService, deployment_job
from backend.app.capabilities.mcp.models import (
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
)
from backend.app.governance.reviews.models import ResourceReview
from backend.app.governance.reviews.service import ResourcePolicyReviewBuilder
from backend.app.identity.auth.service import AuthenticationService
from backend.app.identity.users.models import User
from backend.app.runtime.backends.factory import build_runtime_backend_registry
from backend.app.runtime.instances.contracts import RuntimeCommandResult
from backend.app.runtime.instances.models import RuntimeTemplate
from backend.app.runtime.queues.context import WorkerJobHandlerContext
from backend.app.shared.config import Settings
from backend.app.shared.db.errors import DatabaseConflictError
from backend.app.shared.errors import NotFoundError
from backend.tests.test_mcp_execution import _seed_workspace, _session
from backend.tests.test_runtime_manager import FakeDockerClient
from runtime.opsmesh_runtime.mcp_process import McpProcess


class Queue:
    def __init__(self):
        self.jobs = []

    def ensure_enqueued(self, job):
        self.jobs.append(job)


class Docker(FakeDockerClient):
    def __init__(self):
        super().__init__()
        self.requests = []

    def container_running(self, container_id):
        return container_id in self.started and container_id not in self.stopped

    def start_container(self, container_id):
        self.started.append(container_id)
        if container_id in self.stopped:
            self.stopped.remove(container_id)

    def exec_command(
        self, container_id, command, timeout_seconds, *, input_file=None, working_dir=None
    ):
        request = json.loads(input_file.content)
        self.requests.append(request)
        if request["action"] == "discover":
            result = {"tools": [{"name": "count", "inputSchema": {"type": "object"}}]}
        elif request["action"] == "call":
            result = {"structuredContent": {"calls": 1}}
        else:
            result = {"status": "running"}
        return RuntimeCommandResult(0, json.dumps(result), "")


@pytest.fixture
def managed(monkeypatch):
    session = _session()
    user, workspace = _seed_workspace(session)
    template = RuntimeTemplate(
        name="managed",
        image="python@sha256:" + "0" * 64,
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add(template)
    session.commit()
    settings = Settings(
        runtime_allowed_images=[template.image],
        credential_encryption_secret="test-only-encryption-secret-long-enough",
    )

    def approved(*args, **kwargs):
        return ResourceReview(required=False, risk_level="low", reasons=[], signals={})

    monkeypatch.setattr(ResourcePolicyReviewBuilder, "review_mcp_server", approved)
    monkeypatch.setattr(ResourcePolicyReviewBuilder, "review_mcp_credential_reference", approved)
    queue = Queue()
    service = ManagedMcpService(session, settings, queue)
    auth = AuthenticationService(session).authenticate_user(user.id)
    request = ManagedMcpCreateRequest.model_validate(
        {
            "template_id": str(template.id),
            "mcpServers": {
                "counter": {
                    "command": "python",
                    "args": ["/opt/project/server.py"],
                    "env": {"TEST_PASSWORD": "hidden-value"},
                }
            },
        }
    )
    docker = Docker()
    handler = ManagedMcpJobHandler(
        WorkerJobHandlerContext(
            session=session,
            settings=settings,
            queue=queue,
            runtime_docker_client=docker,
            runtime_backends=build_runtime_backend_registry(docker),
        )
    )
    yield session, workspace, auth, request, service, queue, docker, handler
    session.close()


def test_import_start_reuse_stop_restart_and_secret_boundary(managed):
    session, workspace, auth, request, service, queue, docker, handler = managed
    (deployment,) = service.create(workspace.id, auth, request)
    server = session.get(McpServer, deployment.mcp_server_id)
    credential = session.scalar(select(McpCredentialReference))
    assert not docker.created_requests
    assert "hidden-value" not in repr(request)
    assert "hidden-value" not in json.dumps(server.connection)
    assert "hidden-value" not in credential.encrypted_secret_payload
    assert "hidden-value" not in ManagedMcpResponse.model_validate(deployment).model_dump_json()
    assert "hidden-value" not in queue.jobs[-1].model_dump_json()
    handler.handle(queue.jobs[-1])
    assert deployment.status == "running"
    assert len(docker.created_requests) == 1
    assert session.scalar(select(McpToolAllowlist)).status == "discovered"
    assert docker.requests[0]["server"]["env"]["TEST_PASSWORD"] == "hidden-value"
    handler.handle(queue.jobs[-1])
    assert len(docker.created_requests) == 1
    adapter = ManagedMcpToolAdapter(session, docker)
    assert asyncio.run(
        adapter.call(
            server=server,
            tool_name="count",
            arguments={},
            credential_refs=[credential],
            timeout_seconds=10,
        )
    ) == {"calls": 1}
    service.control(workspace.id, server.id, auth, "stop")
    handler.handle(queue.jobs[-1])
    assert deployment.status == "stopped"
    with pytest.raises(McpExecutionError):
        asyncio.run(
            adapter.call(
                server=server,
                tool_name="count",
                arguments={},
                credential_refs=[credential],
                timeout_seconds=10,
            )
        )
    service.control(workspace.id, server.id, auth, "start")
    handler.handle(queue.jobs[-1])
    assert deployment.status == "running"
    assert len(docker.created_requests) == 1


def test_worker_revalidates_approval_and_workspace_isolation(managed):
    session, workspace, auth, request, service, queue, docker, handler = managed
    (deployment,) = service.create(workspace.id, auth, request)
    with pytest.raises(NotFoundError):
        service.get(uuid4(), deployment.mcp_server_id)
    server = session.get(McpServer, deployment.mcp_server_id)
    server.platform_blocked = True
    session.commit()
    with pytest.raises(RuntimeError, match="Managed MCP lifecycle failed"):
        handler.handle(queue.jobs[-1])
    assert not docker.created_requests
    assert deployment.status == "failed"
    assert server.health_status == "unhealthy"


def test_recovery_after_queue_loss_and_rotated_credentials(managed):
    session, workspace, auth, request, service, queue, docker, handler = managed
    (deployment,) = service.create(workspace.id, auth, request)
    queued_job = deployment_job(deployment)
    queue.jobs.clear()
    reconcile_managed_mcp(session, queue)
    assert queue.jobs[-1].job_id == queued_job.job_id
    handler.handle(queue.jobs[-1])
    credential = session.scalar(select(McpCredentialReference))
    credential.configuration_version += 1
    session.commit()
    server = session.get(McpServer, deployment.mcp_server_id)
    with pytest.raises(McpExecutionError, match="credential rotation"):
        asyncio.run(
            ManagedMcpToolAdapter(session, docker).call(
                server=server,
                tool_name="count",
                arguments={},
                credential_refs=[credential],
                timeout_seconds=10,
            )
        )
    deployment.checked_at = datetime.now(UTC) - timedelta(minutes=2)
    session.commit()
    reconcile_managed_mcp(session, queue)
    handler.handle(queue.jobs[-1])
    assert deployment.credential_version == credential.configuration_version
    assert deployment.status == "running"


def test_revoked_user_stops_long_lived_process_on_refresh(managed):
    session, workspace, auth, request, service, queue, docker, handler = managed
    (deployment,) = service.create(workspace.id, auth, request)
    handler.handle(queue.jobs[-1])
    deployment.checked_at = datetime.now(UTC) - timedelta(minutes=2)
    session.get(User, auth.user_id).status = "disabled"
    session.commit()
    reconcile_managed_mcp(session, queue)
    with pytest.raises(RuntimeError, match="Managed MCP lifecycle failed"):
        handler.handle(queue.jobs[-1])
    assert deployment.status == "failed"
    assert docker.stopped


def test_import_is_atomic_and_approval_does_not_start_code(managed, monkeypatch):
    session, workspace, auth, request, service, queue, docker, handler = managed

    def pending(*args, **kwargs):
        return ResourceReview(required=True, risk_level="high", reasons=["review"], signals={})

    monkeypatch.setattr(ResourcePolicyReviewBuilder, "review_mcp_server", pending)
    (deployment,) = service.create(workspace.id, auth, request)
    assert deployment.status == "pending_approval"
    assert not queue.jobs
    conflicting = request.model_copy(
        update={
            "mcpServers": {
                "new-before-conflict": request.mcpServers["counter"],
                "counter": request.mcpServers["counter"],
            }
        }
    )
    with pytest.raises(DatabaseConflictError):
        service.create(workspace.id, auth, conflicting)
    assert session.scalar(select(McpServer).where(McpServer.name == "new-before-conflict")) is None
    assert not docker.created_requests


def test_persistent_standard_mcp_session_reuses_process_and_recovers():
    async def scenario():
        process = McpProcess(
            {
                "command": sys.executable,
                "args": [str(Path(__file__).parent / "fixtures" / "mcp_persistent_server.py")],
                "env": {"TEST_PASSWORD": "not-in-results"},
            }
        )
        task = asyncio.create_task(process.supervise())

        async def ready():
            async with asyncio.timeout(30):
                while process.state != "running":
                    await asyncio.sleep(0.05)

        async def call(action, **kwargs):
            future = asyncio.get_running_loop().create_future()
            await process.requests.put(({"action": action, **kwargs}, future))
            return await asyncio.wait_for(future, timeout=30)

        try:
            await ready()
            discovery = await call("discover")
            assert {tool["name"] for tool in discovery["tools"]} == {"count", "crash"}
            first = await call("call", name="count", arguments={})
            second = await call("call", name="count", arguments={})
            assert first["structuredContent"]["pid"] == second["structuredContent"]["pid"]
            assert second["structuredContent"]["calls"] == 2
            assert second["structuredContent"]["configured"] is True
            assert "not-in-results" not in json.dumps(discovery)
            crashed = await call("call", name="crash", arguments={})
            assert "error" in crashed
            await ready()
            recovered = await call("call", name="count", arguments={})
            assert recovered["structuredContent"]["pid"] != first["structuredContent"]["pid"]
            assert recovered["structuredContent"]["calls"] == 1
        finally:
            process.stopped.set()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
