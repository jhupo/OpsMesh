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
from backend.app.runtimes.models import RuntimeCommand, RuntimeEvent, RuntimeTemplate
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
    docker = FakeDockerClient()
    manager = RuntimeManager(session, docker)
    limits = RuntimeLimits(cpu_count=1.5, memory_mb=512, disk_mb=1024, timeout_seconds=30)

    runtime = manager.create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="analysis",
        limits=limits,
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
