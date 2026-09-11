from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.core.contracts import RuntimeCommandInputFile, RuntimeCommandResult
from backend.app.runtime_manager.run_environment import RunRuntimeEnvironmentService
from backend.app.runtimes.models import RuntimeEvent, RuntimeTemplate, WorkspaceRuntime
from backend.app.workspaces.models import Workspace


class FakeDockerClient:
    def __init__(self) -> None:
        self.created = []
        self.started: list[str] = []
        self.removed: list[str] = []
        self.removed_volumes: list[str] = []
        self.exec_command_calls: list[tuple[str, list[str], int, str | None]] = []

    def create_container(self, request):  # noqa: ANN001
        self.created.append(request)
        return f"container-{len(self.created)}"

    def start_container(self, container_id: str) -> None:
        self.started.append(container_id)

    def stop_container(self, container_id: str) -> None:
        _ = container_id

    def remove_container(self, container_id: str) -> None:
        self.removed.append(container_id)

    def remove_volume(self, volume_name: str) -> None:
        self.removed_volumes.append(volume_name)

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
        *,
        input_file: RuntimeCommandInputFile | None = None,
        working_dir: str | None = None,
    ) -> RuntimeCommandResult:
        self.exec_command_calls.append((container_id, command, timeout_seconds, working_dir))
        _ = input_file
        return RuntimeCommandResult(exit_code=0, stdout="", stderr="")

    def copy_archive_to_container(
        self,
        container_id: str,
        destination_path: str,
        archive: bytes,
        timeout_seconds: int,
    ) -> None:
        _ = (container_id, destination_path, archive, timeout_seconds)

    def copy_file_from_container(
        self,
        container_id: str,
        source_path: str,
        max_bytes: int,
        timeout_seconds: int,
    ) -> bytes | None:
        _ = (container_id, source_path, max_bytes, timeout_seconds)
        return None


def test_each_managed_run_gets_a_distinct_ephemeral_runtime_and_cleanup() -> None:
    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="run-isolation", settings={})
    template = RuntimeTemplate(
        name="run-isolation-image",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.flush()
    parent = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_template_id=template.id,
        name="shared-placement",
        execution_mode="isolated",
        status="active",
        connection_status="online",
        docker_container_id="parent-container",
        limits={
            "cpu_count": 1,
            "memory_mb": 512,
            "disk_mb": 1024,
            "timeout_seconds": 30,
            "max_output_bytes": 256_000,
            "max_processes": 64,
        },
        network_policy={"mode": "none", "disabled": True},
        capabilities={"isolation": {"workspace_mount": {"target": "/workspace"}}},
    )
    session.add(parent)
    session.flush()
    first = AgentRun(workspace_id=workspace.id, runtime_id=parent.id, status="running", input={})
    second = AgentRun(workspace_id=workspace.id, runtime_id=parent.id, status="running", input={})
    session.add_all([first, second])
    session.commit()

    docker = FakeDockerClient()
    service = RunRuntimeEnvironmentService(session, docker)
    first_result = service.ensure_for_run(first)
    second_result = service.ensure_for_run(second)
    session.commit()

    assert first_result.runtime is not None
    assert second_result.runtime is not None
    assert first_result.runtime.id != second_result.runtime.id
    assert first.execution_runtime_id == first_result.runtime.id
    assert second.execution_runtime_id == second_result.runtime.id
    assert docker.started == ["container-1", "container-2"]
    assert docker.created[0].mounts[0].target == "/workspace"
    assert docker.created[0].mounts[0].source != docker.created[1].mounts[0].source
    assert docker.created[0].labels["opsmesh.run_id"] == str(first.id)
    assert docker.created[1].labels["opsmesh.run_id"] == str(second.id)

    assert service.cleanup_for_run(first) is True
    assert service.cleanup_for_run(first) is True
    assert service.cleanup_for_run(second) is True
    session.commit()
    children = session.scalars(
        select(WorkspaceRuntime).where(
            WorkspaceRuntime.parent_runtime_id == parent.id,
        )
    ).all()
    assert {child.status for child in children} == {"deleted"}
    assert docker.removed == ["container-1", "container-2"]
    assert len(docker.removed_volumes) == 2
    events = session.scalars(select(RuntimeEvent)).all()
    assert {event.event_type for event in events} == {
        "runtime.run.created",
        "runtime.deleted",
    }


def test_pooled_run_reuses_a_preprovisioned_container_and_releases_lease() -> None:
    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="pooled", settings={})
    template = RuntimeTemplate(
        name="pooled-image",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.flush()
    parent = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_template_id=template.id,
        name="pooled-placement",
        execution_mode="pooled",
        pool_key="python-default",
        status="active",
        connection_status="online",
        docker_container_id="pooled-container",
        limits={
            "cpu_count": 1,
            "memory_mb": 512,
            "disk_mb": 1024,
            "timeout_seconds": 30,
            "max_output_bytes": 256_000,
            "max_processes": 64,
        },
        network_policy={"mode": "none", "disabled": True},
        capabilities={
            "isolation": {
                "workspace_mount": {
                    "type": "volume",
                    "docker_volume": "pooled-volume",
                    "target": "/workspace",
                    "mode": "rw",
                },
                "network": {"mode": "none", "disabled": True},
            },
            "hardening": {},
        },
    )
    session.add(parent)
    session.flush()
    run = AgentRun(workspace_id=workspace.id, runtime_id=parent.id, status="running", input={})
    session.add(run)
    session.commit()

    docker = FakeDockerClient()
    service = RunRuntimeEnvironmentService(session, docker)
    result = service.ensure_for_run(run)
    session.commit()

    assert result.runtime is not None
    assert result.runtime.execution_mode == "pooled"
    assert result.runtime.execution_pool_member_id == parent.id
    assert result.runtime.docker_container_id == parent.docker_container_id
    assert docker.created == []
    assert docker.started == []

    assert service.cleanup_for_run(run) is True
    session.commit()
    assert docker.removed == []
    assert docker.removed_volumes == []
    assert len(docker.created) == 0
    assert len(docker.started) == 0
    assert len(docker.removed) == 0
    assert len(docker.removed_volumes) == 0
    assert len(docker.exec_command_calls) == 2


def test_pooled_runs_use_distinct_pool_members_until_a_member_is_released() -> None:
    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="pool-members", settings={})
    template = RuntimeTemplate(
        name="pool-members-image",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.flush()
    limits = {
        "cpu_count": 1,
        "memory_mb": 512,
        "disk_mb": 1024,
        "timeout_seconds": 30,
        "max_output_bytes": 256_000,
        "max_processes": 64,
    }
    capabilities = {
        "isolation": {
            "workspace_mount": {
                "type": "volume",
                "docker_volume": "pool-volume",
                "target": "/workspace",
                "mode": "rw",
            },
            "network": {"mode": "none", "disabled": True},
        },
        "hardening": {},
    }
    members = [
        WorkspaceRuntime(
            workspace_id=workspace.id,
            runtime_template_id=template.id,
            name=f"pool-member-{index}",
            execution_mode="pooled",
            pool_key="shared-python",
            status="active",
            connection_status="online",
            docker_container_id=f"pool-container-{index}",
            limits=limits,
            network_policy={"mode": "none", "disabled": True},
            capabilities=capabilities,
        )
        for index in (1, 2)
    ]
    session.add_all(members)
    session.flush()
    first = AgentRun(
        workspace_id=workspace.id,
        runtime_id=members[0].id,
        status="running",
        input={},
    )
    second = AgentRun(
        workspace_id=workspace.id,
        runtime_id=members[0].id,
        status="running",
        input={},
    )
    session.add_all([first, second])
    session.commit()

    docker = FakeDockerClient()
    service = RunRuntimeEnvironmentService(session, docker)
    first_child = service.ensure_for_run(first).runtime
    second_child = service.ensure_for_run(second).runtime

    assert first_child is not None and second_child is not None
    assert first_child.execution_pool_member_id == members[0].id
    assert second_child.execution_pool_member_id == members[1].id
    assert first_child.docker_container_id != second_child.docker_container_id
    assert docker.created == []


def test_persistent_run_binds_parent_without_child_or_container_cleanup() -> None:
    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="persistent", settings={})
    template = RuntimeTemplate(
        name="persistent-image",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.flush()
    parent = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_template_id=template.id,
        name="persistent-placement",
        execution_mode="persistent",
        status="active",
        connection_status="online",
        docker_container_id="persistent-container",
        limits={"timeout_seconds": 30},
        network_policy={"mode": "none", "disabled": True},
        capabilities={"isolation": {"workspace_mount": {"target": "/workspace"}}},
    )
    session.add(parent)
    session.flush()
    run = AgentRun(workspace_id=workspace.id, runtime_id=parent.id, status="running", input={})
    session.add(run)
    session.commit()

    docker = FakeDockerClient()
    service = RunRuntimeEnvironmentService(session, docker)
    result = service.ensure_for_run(run)
    assert result.runtime is parent
    assert run.execution_runtime_id is None
    assert result.created is True
    assert service.ensure_for_run(run).created is False
    assert service.cleanup_for_run(run) is True
    session.commit()
    assert docker.created == []
    assert docker.removed == []
    assert docker.removed_volumes == []


def test_persistent_runtime_rejects_a_concurrent_run() -> None:
    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="persistent-busy", settings={})
    template = RuntimeTemplate(
        name="persistent-busy-image",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.flush()
    parent = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_template_id=template.id,
        name="persistent-placement",
        execution_mode="persistent",
        status="active",
        connection_status="online",
        docker_container_id="persistent-container",
        limits={"timeout_seconds": 30},
        network_policy={"mode": "none", "disabled": True},
        capabilities={"isolation": {"workspace_mount": {"target": "/workspace"}}},
    )
    session.add(parent)
    session.flush()
    first = AgentRun(workspace_id=workspace.id, runtime_id=parent.id, status="running", input={})
    second = AgentRun(workspace_id=workspace.id, runtime_id=parent.id, status="running", input={})
    session.add_all([first, second])
    session.commit()

    docker = FakeDockerClient()
    service = RunRuntimeEnvironmentService(session, docker)
    service.ensure_for_run(first)
    try:
        service.ensure_for_run(second)
    except RuntimeError as exc:
        assert "already attached" in str(exc)
    else:
        raise AssertionError("A persistent runtime must not be shared concurrently")


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()
