from collections.abc import Generator
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.admin.models import PlatformPolicy
from backend.app.admin.risky_policy_values import RISKY_EXECUTION_POLICY_KEY
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandResult,
    RuntimeCreateRequest,
)
from backend.app.runtime_manager.dependencies import get_docker_runtime_client
from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.runtimes.models import RuntimeEvent, RuntimeTemplate, WorkspaceRuntime
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.jobs import JobType
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


class FakeDockerClient(DockerRuntimeClient):
    def __init__(self) -> None:
        self.created_requests: list[RuntimeCreateRequest] = []
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.removed: list[str] = []
        self.executed: list[tuple[str, list[str], int]] = []

    def create_container(self, request: RuntimeCreateRequest) -> str:
        self.created_requests.append(request)
        return "container-123"

    def start_container(self, container_id: str) -> None:
        self.started.append(container_id)

    def stop_container(self, container_id: str) -> None:
        self.stopped.append(container_id)

    def remove_container(self, container_id: str) -> None:
        self.removed.append(container_id)

    def remove_volume(self, volume_name: str) -> None:
        return None

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
    ) -> RuntimeCommandResult:
        self.executed.append((container_id, command, timeout_seconds))
        return RuntimeCommandResult(exit_code=0, stdout="ok\n", stderr="")


def test_runtime_api_lifecycle_and_workspace_scope() -> None:
    client, session, docker, queue = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="other-space",
    )
    template = _seed_template(session)
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team Space",
        scope="workspace",
        policy={},
        network_policy={"mode": "none"},
        storage_policy={},
        cleanup_policy={},
    )
    session.add(runtime_space)
    session.commit()

    templates = client.get(
        f"/api/v1/workspaces/{workspace.id}/runtime-templates",
        headers=_headers(owner.id),
    )
    assert templates.status_code == 200
    assert templates.json()[0]["id"] == str(template.id)

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtimes",
        headers=_headers(owner.id),
        json={
            "template_id": str(template.id),
            "name": "personal-python",
            "runtime_space_id": str(runtime_space.id),
            "limits": {
                "cpu_count": 1,
                "memory_mb": 512,
                "disk_mb": 1024,
                "timeout_seconds": 20,
                "max_output_bytes": 1024,
                "max_processes": 64,
            },
        },
    )
    assert created.status_code == 201
    runtime_id = created.json()["id"]
    assert created.json()["network_policy"] == {"disabled": True}
    assert created.json()["runtime_space_id"] == str(runtime_space.id)
    assert created.json()["status"] == "queued"
    assert created.json()["has_docker_container"] is False
    assert "docker_container_id" not in created.json()
    assert created.json()["limits"]["max_output_bytes"] == 1024
    assert created.json()["limits"]["max_processes"] == 64
    assert docker.created_requests == []
    create_job = queue.dequeue()
    assert create_job is not None
    assert create_job.job_type == JobType.RUNTIME_CONTROL
    assert create_job.routing["action"] == "create"
    WorkerJobHandler(
        session,
        settings=client.app.state.settings,
        runtime_docker_client=docker,
    ).handle(create_job)
    queue.ack(create_job)
    assert docker.created_requests[0].image == "python:3.12-slim"
    assert docker.created_requests[0].network_disabled is True
    assert docker.created_requests[0].hardening.cap_drop == ("ALL",)
    assert docker.created_requests[0].hardening.security_opt == ("no-new-privileges:true",)
    assert docker.created_requests[0].hardening.read_only_rootfs is True

    forbidden = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/runtimes/{runtime_id}/start",
        headers=_headers(other.id),
    )
    assert forbidden.status_code == 404

    started = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtimes/{runtime_id}/start",
        headers=_headers(owner.id),
    )
    assert started.status_code == 200
    assert started.json()["status"] == "created"
    assert started.json()["has_docker_container"] is True
    start_job = queue.dequeue()
    assert start_job is not None
    assert start_job.routing["action"] == "start"
    WorkerJobHandler(
        session,
        settings=client.app.state.settings,
        runtime_docker_client=docker,
    ).handle(start_job)
    queue.ack(start_job)

    command = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtimes/{runtime_id}/commands",
        headers=_headers(owner.id),
        json={"command": ["python", "--version"]},
    )
    assert command.status_code == 201
    assert command.json()["status"] == "queued"
    command_job = queue.dequeue()
    assert command_job is not None
    assert command_job.routing["action"] == "command"
    WorkerJobHandler(
        session,
        settings=client.app.state.settings,
        runtime_docker_client=docker,
    ).handle(command_job)
    queue.ack(command_job)

    commands = client.get(
        f"/api/v1/workspaces/{workspace.id}/runtimes/{runtime_id}/commands",
        headers=_headers(owner.id),
    )
    events = client.get(
        f"/api/v1/workspaces/{workspace.id}/runtimes/{runtime_id}/events",
        headers=_headers(owner.id),
    )
    stopped = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtimes/{runtime_id}/stop",
        headers=_headers(owner.id),
    )
    assert stopped.status_code == 200
    stop_job = queue.dequeue()
    assert stop_job is not None
    assert stop_job.routing["action"] == "stop"
    WorkerJobHandler(
        session,
        settings=client.app.state.settings,
        runtime_docker_client=docker,
    ).handle(stop_job)
    queue.ack(stop_job)

    deleted = client.delete(
        f"/api/v1/workspaces/{workspace.id}/runtimes/{runtime_id}",
        headers=_headers(owner.id),
    )
    assert deleted.status_code == 204
    delete_job = queue.dequeue()
    assert delete_job is not None
    assert delete_job.routing["action"] == "delete"
    WorkerJobHandler(
        session,
        settings=client.app.state.settings,
        runtime_docker_client=docker,
    ).handle(delete_job)
    queue.ack(delete_job)

    assert commands.status_code == 200
    assert commands.json()["total"] == 1
    assert commands.json()["items"][0]["stdout"] == "ok\n"
    assert events.status_code == 200
    assert events.json()["total"] >= 2
    assert docker.started == ["container-123"]
    assert docker.executed == [("container-123", ["python", "--version"], 20)]
    assert docker.stopped == ["container-123"]
    assert docker.removed == ["container-123"]


def test_runtime_api_rejects_disallowed_image_and_network() -> None:
    client, session, docker, _ = _client(allowed_images=["python:3.12-slim"])
    owner, workspace = _seed_workspace(session, role="owner")
    allowed_template = _seed_template(session)
    blocked_template = _seed_template(
        session,
        name="unsafe",
        image="ubuntu:24.04",
        network_policy={"disabled": True},
    )

    blocked_image = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtimes",
        headers=_headers(owner.id),
        json={"template_id": str(blocked_template.id), "name": "unsafe"},
    )
    blocked_network = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtimes",
        headers=_headers(owner.id),
        json={
            "template_id": str(allowed_template.id),
            "name": "networked",
            "network_disabled": False,
        },
    )

    assert blocked_image.status_code == 400
    assert blocked_image.json()["error"]["code"] == "bad_request"
    assert "image is not allowed" in blocked_image.json()["error"]["message"]
    assert blocked_network.status_code == 400
    assert blocked_network.json()["error"]["code"] == "bad_request"
    assert "network access is disabled" in blocked_network.json()["error"]["message"]
    assert docker.created_requests == []


def test_runtime_api_rejects_cross_workspace_runtime_space() -> None:
    client, session, docker, _ = _client(allowed_images=["python:3.12-slim"])
    owner, workspace = _seed_workspace(session, role="owner")
    _, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="other-space",
    )
    template = _seed_template(session)
    runtime_space = RuntimeSpace(
        workspace_id=other_workspace.id,
        name="Other Space",
        scope="workspace",
        policy={},
        network_policy={"mode": "none"},
        storage_policy={},
        cleanup_policy={},
    )
    session.add(runtime_space)
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtimes",
        headers=_headers(owner.id),
        json={
            "template_id": str(template.id),
            "name": "cross-space",
            "runtime_space_id": str(runtime_space.id),
        },
    )

    assert response.status_code == 400
    assert "Runtime space not found" in response.json()["error"]["message"]
    assert docker.created_requests == []


def test_runtime_events_redact_sensitive_metadata() -> None:
    client, session, _, _ = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="runtime",
        docker_container_id="container-secret",
    )
    session.add(runtime)
    session.flush()
    session.add(
        RuntimeEvent(
            workspace_id=workspace.id,
            workspace_runtime_id=runtime.id,
            event_type="runtime.cleanup",
            message="cleanup",
            event_metadata={
                "cleanup": {
                    "container_id": "container-secret",
                    "headers": {"authorization": "Bearer hidden"},
                },
                "safe": "visible",
            },
            created_at=datetime.now(UTC),
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/runtimes/{runtime.id}/events",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    metadata = response.json()["items"][0]["event_metadata"]
    assert metadata == {
        "cleanup": {"container_id": "[redacted]", "headers": "[redacted]"},
        "safe": "visible",
    }
    assert "container-secret" not in str(metadata)
    assert "Bearer hidden" not in str(metadata)


def test_runtime_responses_redact_sensitive_policy_fields() -> None:
    client, session, _, _ = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    template = _seed_template(session)
    template.default_limits = {
        "cpu_count": 1,
        "api_key": "sk-template",
        "nested": {"base_url": "https://template.example.test/private"},
    }
    template.default_network_policy = {
        "mode": "restricted",
        "headers": {"authorization": "Bearer template"},
    }
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_template_id=template.id,
        name="runtime",
        docker_container_id="container-secret",
        limits={"memory_mb": 512, "token": "runtime-token"},
        network_policy={"remote_url": "https://runtime.example.test/private"},
        capabilities={"mcp": {"headers": {"authorization": "Bearer runtime"}}},
    )
    session.add(runtime)
    session.commit()

    templates = client.get(
        f"/api/v1/workspaces/{workspace.id}/runtime-templates",
        headers=_headers(owner.id),
    )
    runtimes = client.get(
        f"/api/v1/workspaces/{workspace.id}/runtimes",
        headers=_headers(owner.id),
    )

    assert templates.status_code == 200
    template_body = templates.json()[0]
    assert template_body["default_limits"]["api_key"] == "[redacted]"
    assert template_body["default_limits"]["nested"]["base_url"] == "[redacted]"
    assert template_body["default_network_policy"]["headers"] == "[redacted]"
    assert runtimes.status_code == 200
    runtime_body = runtimes.json()["items"][0]
    assert runtime_body["limits"]["token"] == "[redacted]"
    assert runtime_body["network_policy"]["remote_url"] == "[redacted]"
    assert runtime_body["capabilities"]["mcp"]["headers"] == "[redacted]"
    assert "docker_container_id" not in runtime_body
    assert "sk-template" not in str(template_body)
    assert "runtime-token" not in str(runtime_body)
    assert "Bearer runtime" not in str(runtime_body)


def test_runtime_api_allows_network_when_template_allows_it() -> None:
    client, session, docker, queue = _client(allowed_images=["python:3.12-slim"])
    owner, workspace = _seed_workspace(session, role="owner")
    template = _seed_template(session, network_policy={"allow_network": True})
    session.add(
        PlatformPolicy(
            policy_key=RISKY_EXECUTION_POLICY_KEY,
            value={
                "allow_runtime_commands": True,
                "allow_network_egress": True,
                "allow_self_hosted_runtimes": True,
                "require_approval_for_high_risk_tools": True,
            },
            description="test",
        )
    )
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtimes",
        headers=_headers(owner.id),
        json={
            "template_id": str(template.id),
            "name": "networked",
            "network_disabled": False,
        },
    )

    assert response.status_code == 201
    assert response.json()["network_policy"] == {"disabled": False}
    job = queue.dequeue()
    assert job is not None
    WorkerJobHandler(
        session,
        settings=client.app.state.settings,
        runtime_docker_client=docker,
    ).handle(job)
    queue.ack(job)
    assert docker.created_requests[0].network_disabled is False


def test_runtime_api_rejects_network_when_platform_policy_disables_egress() -> None:
    client, session, docker, _ = _client(allowed_images=["python:3.12-slim"])
    owner, workspace = _seed_workspace(session, role="owner")
    template = _seed_template(session, network_policy={"allow_network": True})
    session.add(
        PlatformPolicy(
            policy_key=RISKY_EXECUTION_POLICY_KEY,
            value={
                "allow_runtime_commands": True,
                "allow_network_egress": False,
                "allow_self_hosted_runtimes": True,
                "require_approval_for_high_risk_tools": True,
            },
            description="test",
        )
    )
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtimes",
        headers=_headers(owner.id),
        json={
            "template_id": str(template.id),
            "name": "networked",
            "network_disabled": False,
        },
    )

    assert response.status_code == 400
    assert "platform safety policy" in response.json()["error"]["message"]
    assert docker.created_requests == []


def _client(
    *,
    allowed_images: list[str] | None = None,
) -> tuple[TestClient, Session, FakeDockerClient, RedisQueue]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed_session = session_factory()
    docker = FakeDockerClient()
    settings = Settings(
        environment="test",
        log_format="text",
        internal_api_token=TOKEN,
        database_url="sqlite+pysqlite:///:memory:",
        runtime_allowed_images=allowed_images or ["python:3.12-slim"],
    )
    app = create_app(settings)
    from fakeredis import FakeRedis

    from backend.app.redis.keys import RedisKeyBuilder
    from backend.app.workers.dependencies import get_worker_queue

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_docker_runtime_client] = lambda: docker
    queue = RedisQueue(
        FakeRedis(decode_responses=True),
        RedisKeyBuilder("opsmesh"),
        "agent_runs",
        0,
    )
    app.dependency_overrides[get_worker_queue] = lambda: queue
    return TestClient(app), seed_session, docker, queue


def _seed_workspace(
    session: Session,
    *,
    role: str,
    email: str = "owner@example.com",
    slug: str = "owner-space",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role=role)
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _seed_template(
    session: Session,
    *,
    name: str = "python",
    image: str = "python:3.12-slim",
    network_policy: dict[str, object] | None = None,
) -> RuntimeTemplate:
    template = RuntimeTemplate(
        name=f"{name}-{uuid4()}",
        image=image,
        default_limits={
            "cpu_count": 1,
            "memory_mb": 512,
            "disk_mb": 1024,
            "timeout_seconds": 60,
        },
        default_network_policy=network_policy or {"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add(template)
    session.commit()
    return template


def _headers(user_id: object) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-User-ID": str(user_id),
    }


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
