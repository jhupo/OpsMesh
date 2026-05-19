from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandResult,
    RuntimeCreateRequest,
    RuntimeLimits,
)
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtime_manager.quotas import RuntimeQuotaExceededError, RuntimeQuotaPolicy
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceEvent
from backend.app.runtimes.models import (
    RuntimeCommand,
    RuntimeEvent,
    RuntimeTemplate,
    WorkspaceRuntime,
)
from backend.app.workspaces.models import Workspace


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

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
    ) -> RuntimeCommandResult:
        self.executed.append((container_id, command, timeout_seconds))
        return RuntimeCommandResult(exit_code=0, stdout="ok\n", stderr="")


def test_runtime_manager_lifecycle_and_command_execution() -> None:
    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="acme", settings={})
    template = RuntimeTemplate(
        name="python",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.commit()
    runtime_space = RuntimeSpace(workspace_id=workspace.id, name="Team Space", scope="workspace")
    session.add(runtime_space)
    session.commit()
    docker = FakeDockerClient()
    manager = RuntimeManager(session, docker)
    limits = RuntimeLimits(cpu_count=1.5, memory_mb=512, disk_mb=1024, timeout_seconds=30)

    runtime = manager.create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="analysis",
        limits=limits,
        runtime_space_id=runtime_space.id,
    )
    manager.start_runtime(runtime)
    command = manager.execute_command(
        workspace_id=workspace.id,
        runtime=runtime,
        command=["python", "--version"],
    )
    manager.stop_runtime(runtime)
    manager.delete_runtime(runtime)

    events = session.scalars(
        select(RuntimeEvent)
        .where(RuntimeEvent.workspace_runtime_id == runtime.id)
        .order_by(RuntimeEvent.created_at)
    ).all()
    space_events = session.scalars(
        select(RuntimeSpaceEvent)
        .where(RuntimeSpaceEvent.runtime_space_id == runtime_space.id)
        .order_by(RuntimeSpaceEvent.created_at)
    ).all()

    assert docker.created_requests[0].limits == limits
    assert docker.created_requests[0].network_disabled is True
    assert docker.created_requests[0].workspace_id == str(workspace.id)
    assert docker.started == ["container-123"]
    assert docker.executed == [("container-123", ["python", "--version"], 30)]
    assert docker.stopped == ["container-123"]
    assert docker.removed == ["container-123"]
    assert command.status == "completed"
    assert command.stdout == "ok\n"
    assert session.query(RuntimeCommand).count() == 1
    assert [event.event_type for event in events] == [
        "runtime.created",
        "runtime.started",
        "runtime.command.completed",
        "runtime.stopped",
        "runtime.deleted",
    ]
    assert [event.event_type for event in space_events] == [
        "runtime.created",
        "runtime.started",
        "runtime.command.completed",
        "runtime.stopped",
        "runtime.deleted",
    ]
    assert space_events[0].event_metadata["runtime_id"] == str(runtime.id)
    assert space_events[0].event_metadata["runtime_status"] == "created"


def test_runtime_manager_rejects_cross_workspace_command() -> None:
    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="acme", settings={})
    template = RuntimeTemplate(
        name="python",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.commit()
    runtime = RuntimeManager(session, FakeDockerClient()).create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="analysis",
        limits=RuntimeLimits(cpu_count=1, memory_mb=256, disk_mb=512, timeout_seconds=10),
    )

    try:
        RuntimeManager(session, FakeDockerClient()).execute_command(
            workspace_id=uuid4(),
            runtime=runtime,
            command=["echo", "bad"],
        )
    except PermissionError as exc:
        assert "does not belong" in str(exc)
    else:
        raise AssertionError("Expected cross-workspace runtime command to fail")


def test_cleanup_stale_runtime_removes_only_recorded_container() -> None:
    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="acme", settings={})
    template = RuntimeTemplate(
        name="python",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.commit()
    docker = FakeDockerClient()
    runtime = RuntimeManager(session, docker).create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="analysis",
        limits=RuntimeLimits(cpu_count=1, memory_mb=256, disk_mb=512, timeout_seconds=10),
    )
    runtime.status = "stopped"
    session.commit()

    RuntimeManager(session, docker).cleanup_stale_runtime(runtime)

    assert docker.removed == ["container-123"]
    assert runtime.status == "deleted"


def test_runtime_manager_rejects_single_runtime_over_workspace_quota() -> None:
    session = _session()
    workspace = Workspace(
        owner_user_id=uuid4(),
        name="Acme",
        slug="acme",
        settings={"runtime_quota": {"max_runtime_memory_mb": 512}},
    )
    template = RuntimeTemplate(
        name="python",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.commit()
    docker = FakeDockerClient()

    try:
        RuntimeManager(session, docker).create_runtime(
            workspace_id=workspace.id,
            template=template,
            name="oversized",
            limits=RuntimeLimits(cpu_count=1, memory_mb=1024, disk_mb=512, timeout_seconds=10),
        )
    except RuntimeQuotaExceededError as exc:
        assert exc.code == "runtime_memory_quota_exceeded"
    else:
        raise AssertionError("Expected runtime memory quota to fail")

    assert docker.created_requests == []
    assert session.query(WorkspaceRuntime).count() == 0


def test_runtime_quota_policy_counts_active_workspace_usage() -> None:
    session = _session()
    workspace = Workspace(
        owner_user_id=uuid4(),
        name="Acme",
        slug="acme",
        settings={
            "runtime_quota": {
                "max_active_runtimes": 2,
                "max_total_cpu": 2,
                "max_total_memory_mb": 1024,
                "max_total_disk_mb": 2048,
            }
        },
    )
    session.add(workspace)
    session.flush()
    session.add_all(
        [
            WorkspaceRuntime(
                workspace_id=workspace.id,
                name="active",
                status="running",
                limits={"cpu_count": 1, "memory_mb": 512, "disk_mb": 512},
            ),
            WorkspaceRuntime(
                workspace_id=workspace.id,
                name="deleted",
                status="deleted",
                limits={"cpu_count": 8, "memory_mb": 8192, "disk_mb": 8192},
            ),
        ]
    )
    session.commit()

    usage = RuntimeQuotaPolicy(session).usage_for_workspace(workspace.id)

    assert usage.active_runtimes == 1
    assert usage.total_cpu == 1
    assert usage.total_memory_mb == 512
    assert usage.total_disk_mb == 512


def test_runtime_manager_rejects_total_cpu_over_workspace_quota() -> None:
    session = _session()
    workspace = Workspace(
        owner_user_id=uuid4(),
        name="Acme",
        slug="acme",
        settings={"runtime_quota": {"max_total_cpu": 1.5}},
    )
    template = RuntimeTemplate(
        name="python",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.flush()
    session.add(
        WorkspaceRuntime(
            workspace_id=workspace.id,
            name="existing",
            status="running",
            limits={"cpu_count": 1, "memory_mb": 256, "disk_mb": 512},
        )
    )
    session.commit()
    docker = FakeDockerClient()

    try:
        RuntimeManager(session, docker).create_runtime(
            workspace_id=workspace.id,
            template=template,
            name="too-much-cpu",
            limits=RuntimeLimits(cpu_count=1, memory_mb=256, disk_mb=512, timeout_seconds=10),
        )
    except RuntimeQuotaExceededError as exc:
        assert exc.code == "runtime_total_cpu_quota_exceeded"
    else:
        raise AssertionError("Expected total CPU quota to fail")

    assert docker.created_requests == []


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
