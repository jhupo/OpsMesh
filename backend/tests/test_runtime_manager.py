from datetime import UTC, datetime
from pathlib import Path
from subprocess import TimeoutExpired
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandResult,
    RuntimeCreateRequest,
    RuntimeLimits,
    RuntimeMount,
)
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtime_manager.quotas import RuntimeQuotaExceededError, RuntimeQuotaPolicy
from backend.app.runtime_manager.service import RuntimeControlService
from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceBinding,
    RuntimeSpaceEvent,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.runtime_spaces.service import RuntimeSpaceService
from backend.app.runtimes.models import (
    RuntimeCommand,
    RuntimeEvent,
    RuntimeLease,
    RuntimeTemplate,
    WorkspaceRuntime,
)
from backend.app.security.models import SecurityEvent
from backend.app.teams.models import AgentTeam
from backend.app.workspaces.models import Workspace


class FakeDockerClient(DockerRuntimeClient):
    def __init__(self) -> None:
        self.created_requests: list[RuntimeCreateRequest] = []
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.removed: list[str] = []
        self.removed_volumes: list[str] = []
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
        self.removed_volumes.append(volume_name)

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
        *,
        stdin_data: str | None = None,
    ) -> RuntimeCommandResult:
        _ = stdin_data
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
    limits = RuntimeLimits(
        cpu_count=1.5,
        memory_mb=512,
        disk_mb=1024,
        timeout_seconds=30,
        max_processes=64,
    )

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
    assert docker.created_requests[0].hardening.cap_drop == ("ALL",)
    assert docker.created_requests[0].hardening.security_opt == ("no-new-privileges:true",)
    assert docker.created_requests[0].hardening.read_only_rootfs is True
    assert [tmpfs.target for tmpfs in docker.created_requests[0].hardening.tmpfs] == [
        "/tmp",
        "/var/tmp",
    ]
    assert docker.created_requests[0].hardening.user_enforced is False
    assert docker.created_requests[0].workspace_id == str(workspace.id)
    assert docker.created_requests[0].runtime_id == str(runtime.id)
    assert docker.created_requests[0].runtime_space_id == str(runtime_space.id)
    assert docker.created_requests[0].working_dir == "/workspace"
    assert docker.created_requests[0].mounts[0].target == "/workspace"
    assert docker.created_requests[0].mounts[0].source.startswith("opsmesh-ws-")
    assert docker.created_requests[0].labels["opsmesh.managed"] == "true"
    assert docker.created_requests[0].limits.max_processes == 64
    assert docker.started == ["container-123"]
    assert docker.executed == [("container-123", ["python", "--version"], 30)]
    assert docker.stopped == ["container-123"]
    assert docker.removed == ["container-123"]
    assert docker.removed_volumes == [docker.created_requests[0].mounts[0].source]
    assert command.status == "completed"
    assert command.stdout == "ok\n"
    assert session.query(RuntimeCommand).count() == 1
    lease = session.scalar(
        select(RuntimeLease).where(RuntimeLease.workspace_runtime_id == runtime.id)
    )
    assert lease is not None
    assert lease.status == "released"
    assert lease.released_at is not None
    assert lease.docker_container_id == "container-123"
    assert lease.lease_metadata["hardening"]["cap_drop"] == ["ALL"]
    assert lease.lease_metadata["hardening"]["read_only_rootfs"] is True
    assert [event.event_type for event in events] == [
        "runtime.created",
        "runtime.lease_acquired",
        "runtime.started",
        "runtime.command.completed",
        "runtime.stopped",
        "runtime.deleted",
    ]
    assert events[0].event_metadata["isolation"]["workspace_mount"]["target"] == "/workspace"
    assert events[0].event_metadata["hardening"]["cap_drop"] == ["ALL"]
    assert events[0].event_metadata["hardening"]["security_opt"] == [
        "no-new-privileges:true"
    ]
    assert events[0].event_metadata["hardening"]["read_only_rootfs"] is True
    assert events[0].event_metadata["hardening"]["user"] == {
        "value": None,
        "policy": "image_default",
        "enforced": False,
    }
    assert events[1].event_metadata["runtime_lease_id"] == str(lease.id)
    assert events[2].event_metadata["runtime_lease_id"] == str(lease.id)
    assert events[-1].event_metadata["cleanup"]["action"] == "delete"
    assert events[-1].event_metadata["cleanup"]["container_id"] == "container-123"
    assert events[-1].event_metadata["cleanup"]["success"] is True
    assert [event.event_type for event in space_events] == [
        "runtime_space.reserved",
        "runtime.created",
        "runtime.lease_acquired",
        "runtime.started",
        "runtime.command.completed",
        "runtime.stopped",
        "runtime.deleted",
        "runtime_space.reservation_released",
    ]
    assert space_events[1].event_metadata["runtime_id"] == str(runtime.id)
    assert space_events[1].event_metadata["runtime_status"] == "created"
    assert runtime.capabilities["isolation"]["workspace_id"] == str(workspace.id)
    assert runtime.capabilities["isolation"]["runtime_space_id"] == str(runtime_space.id)
    assert runtime.capabilities["hardening"]["writable_paths"] == [
        {"type": "volume", "target": "/workspace", "mode": "rw"},
        {
            "type": "tmpfs",
            "target": "/tmp",
            "mode": "rw,noexec,nosuid,nodev",
            "size_mb": 64,
        },
        {
            "type": "tmpfs",
            "target": "/var/tmp",
            "mode": "rw,noexec,nosuid,nodev",
            "size_mb": 16,
        },
    ]
    assert runtime.capabilities["managed_resources"]["docker_volumes"] == [
        docker.created_requests[0].mounts[0].source
    ]


def test_runtime_manager_passes_stdin_without_persisting_it() -> None:
    class StdinDockerClient(FakeDockerClient):
        stdin_values: list[str | None] = []

        def exec_command(
            self,
            container_id: str,
            command: list[str],
            timeout_seconds: int,
            *,
            stdin_data: str | None = None,
        ) -> RuntimeCommandResult:
            self.stdin_values.append(stdin_data)
            return super().exec_command(
                container_id,
                command,
                timeout_seconds,
                stdin_data=stdin_data,
            )

    session = _session()
    workspace = Workspace(
        owner_user_id=uuid4(),
        name="Acme",
        slug="acme-transient-stdin",
        settings={},
    )
    template = RuntimeTemplate(
        name="python-stdin",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.commit()
    docker = StdinDockerClient()
    manager = RuntimeManager(session, docker)
    runtime = manager.create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="analysis",
        limits=RuntimeLimits(
            cpu_count=1,
            memory_mb=256,
            disk_mb=512,
            timeout_seconds=10,
        ),
    )

    record = manager.execute_command(
        workspace_id=workspace.id,
        runtime=runtime,
        command=["python", "-m", "worker", "--request-stdin"],
        stdin_data='{"env":{"MCP_API_KEY":"runtime-secret"}}',
    )

    assert docker.stdin_values == ['{"env":{"MCP_API_KEY":"runtime-secret"}}']
    assert "runtime-secret" not in str(record.command)
    assert "runtime-secret" not in str(record.stderr)


def test_runtime_manager_reserves_and_releases_runtime_space_docker_usage() -> None:
    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="acme-slots", settings={})
    template = RuntimeTemplate(
        name="python-slots",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.flush()
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Docker Slots",
        scope="workspace",
    )
    session.add(runtime_space)
    session.flush()
    session.add_all(
        [
            RuntimeSpaceQuota(
                workspace_id=workspace.id,
                runtime_space_id=runtime_space.id,
                quota_key="docker_runtimes",
                limit_value=1,
            ),
            RuntimeSpaceQuota(
                workspace_id=workspace.id,
                runtime_space_id=runtime_space.id,
                quota_key="cpu",
                limit_value=2,
            ),
            RuntimeSpaceQuota(
                workspace_id=workspace.id,
                runtime_space_id=runtime_space.id,
                quota_key="memory_mb",
                limit_value=1024,
            ),
            RuntimeSpaceQuota(
                workspace_id=workspace.id,
                runtime_space_id=runtime_space.id,
                quota_key="storage_mb",
                limit_value=2048,
            ),
        ]
    )
    session.commit()
    manager = RuntimeManager(session, FakeDockerClient())

    runtime = manager.create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="analysis",
        limits=RuntimeLimits(cpu_count=1.5, memory_mb=512, disk_mb=1024, timeout_seconds=10),
        runtime_space_id=runtime_space.id,
    )
    quotas_after_create = _runtime_space_quotas(session, runtime_space.id)
    reservation = session.query(RuntimeSpaceReservation).one()

    assert quotas_after_create == {
        "docker_runtimes": 1,
        "cpu": 2,
        "memory_mb": 512,
        "storage_mb": 1024,
    }
    assert reservation.status == "active"
    assert reservation.reservation_key == f"workspace_runtime:{runtime.id}:docker"
    assert reservation.resource_usage == {
        "docker_runtimes": 1,
        "cpu": 2,
        "memory_mb": 512,
        "storage_mb": 1024,
    }

    manager.delete_runtime(runtime)

    session.refresh(reservation)
    assert _runtime_space_quotas(session, runtime_space.id) == {
        "docker_runtimes": 0,
        "cpu": 0,
        "memory_mb": 0,
        "storage_mb": 0,
    }
    assert reservation.status == "released"
    assert reservation.released_at is not None


def test_runtime_manager_releases_runtime_space_reservation_when_docker_create_fails() -> None:
    class FailingCreateDockerClient(FakeDockerClient):
        def create_container(self, request: RuntimeCreateRequest) -> str:
            self.created_requests.append(request)
            raise RuntimeError("docker create failed")

    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="acme-create-fail", settings={})
    template = RuntimeTemplate(
        name="python-create-fail",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.flush()
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Docker Slots",
        scope="workspace",
    )
    session.add(runtime_space)
    session.flush()
    session.add(
        RuntimeSpaceQuota(
            workspace_id=workspace.id,
            runtime_space_id=runtime_space.id,
            quota_key="docker_runtimes",
            limit_value=1,
        )
    )
    session.commit()
    docker = FailingCreateDockerClient()

    try:
        RuntimeManager(session, docker).create_runtime(
            workspace_id=workspace.id,
            template=template,
            name="analysis",
            limits=RuntimeLimits(cpu_count=1, memory_mb=256, disk_mb=512, timeout_seconds=10),
            runtime_space_id=runtime_space.id,
        )
    except RuntimeError as exc:
        assert "docker create failed" in str(exc)
    else:
        raise AssertionError("Expected Docker create failure")

    reservation = session.query(RuntimeSpaceReservation).one()
    assert docker.created_requests
    assert session.query(WorkspaceRuntime).count() == 0
    assert _runtime_space_quotas(session, runtime_space.id) == {"docker_runtimes": 0}
    assert reservation.status == "released"


def test_runtime_manager_rejects_runtime_space_docker_quota_before_container_create() -> None:
    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="acme-quota-block", settings={})
    template = RuntimeTemplate(
        name="python-quota-block",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.flush()
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Docker Slots",
        scope="workspace",
    )
    session.add(runtime_space)
    session.flush()
    session.add(
        RuntimeSpaceQuota(
            workspace_id=workspace.id,
            runtime_space_id=runtime_space.id,
            quota_key="docker_runtimes",
            limit_value=0,
        )
    )
    session.commit()
    docker = FakeDockerClient()

    try:
        RuntimeManager(session, docker).create_runtime(
            workspace_id=workspace.id,
            template=template,
            name="analysis",
            limits=RuntimeLimits(cpu_count=1, memory_mb=256, disk_mb=512, timeout_seconds=10),
            runtime_space_id=runtime_space.id,
        )
    except RuntimeQuotaExceededError as exc:
        assert exc.code == "runtime_space_quota_exceeded:docker_runtimes"
    else:
        raise AssertionError("Expected runtime-space Docker quota failure")

    assert docker.created_requests == []
    assert session.query(WorkspaceRuntime).count() == 0
    assert session.query(RuntimeSpaceReservation).count() == 0


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


def test_runtime_manager_records_command_timeout_without_leaving_running_command() -> None:
    class TimeoutDockerClient(FakeDockerClient):
        def exec_command(
            self,
            container_id: str,
            command: list[str],
            timeout_seconds: int,
            *,
            stdin_data: str | None = None,
        ) -> RuntimeCommandResult:
            _ = stdin_data
            self.executed.append((container_id, command, timeout_seconds))
            raise TimeoutExpired(cmd=command, timeout=timeout_seconds)

    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="acme-timeout", settings={})
    template = RuntimeTemplate(
        name="python",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.commit()
    docker = TimeoutDockerClient()
    runtime = RuntimeManager(session, docker).create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="analysis",
        limits=RuntimeLimits(cpu_count=1, memory_mb=256, disk_mb=512, timeout_seconds=3),
    )

    command = RuntimeManager(session, docker).execute_command(
        workspace_id=workspace.id,
        runtime=runtime,
        command=["python", "slow.py"],
    )

    event = session.scalar(
        select(RuntimeEvent).where(
            RuntimeEvent.workspace_runtime_id == runtime.id,
            RuntimeEvent.event_type == "runtime.command.timeout",
        )
    )
    assert docker.executed == [("container-123", ["python", "slow.py"], 3)]
    assert command.status == "timeout"
    assert command.completed_at is not None
    assert "exceeded timeout" in command.stderr
    assert event is not None
    assert event.event_metadata["command_id"] == str(command.id)
    assert event.event_metadata["reason"] == "timeout"


def test_runtime_manager_records_docker_exec_failure_without_raising() -> None:
    class FailingExecDockerClient(FakeDockerClient):
        def exec_command(
            self,
            container_id: str,
            command: list[str],
            timeout_seconds: int,
            *,
            stdin_data: str | None = None,
        ) -> RuntimeCommandResult:
            _ = stdin_data
            self.executed.append((container_id, command, timeout_seconds))
            raise RuntimeError("docker exec unavailable")

    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="acme-exec-fail", settings={})
    template = RuntimeTemplate(
        name="python",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.commit()
    docker = FailingExecDockerClient()
    runtime = RuntimeManager(session, docker).create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="analysis",
        limits=RuntimeLimits(cpu_count=1, memory_mb=256, disk_mb=512, timeout_seconds=10),
    )

    command = RuntimeManager(session, docker).execute_command(
        workspace_id=workspace.id,
        runtime=runtime,
        command=["python", "job.py"],
    )

    event = session.scalar(
        select(RuntimeEvent).where(
            RuntimeEvent.workspace_runtime_id == runtime.id,
            RuntimeEvent.event_type == "runtime.command.failed",
        )
    )
    assert command.status == "failed"
    assert command.completed_at is not None
    assert command.stderr == "docker exec unavailable"
    assert event is not None
    assert event.event_metadata["reason"] == "docker_exec_failed"


def test_runtime_manager_limits_command_output_and_records_policy_event() -> None:
    class NoisyDockerClient(FakeDockerClient):
        def exec_command(
            self,
            container_id: str,
            command: list[str],
            timeout_seconds: int,
            *,
            stdin_data: str | None = None,
        ) -> RuntimeCommandResult:
            _ = stdin_data
            self.executed.append((container_id, command, timeout_seconds))
            return RuntimeCommandResult(exit_code=0, stdout="abcdef", stderr="xyz")

    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="acme-output", settings={})
    template = RuntimeTemplate(
        name="python",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.commit()
    docker = NoisyDockerClient()
    runtime = RuntimeManager(session, docker).create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="analysis",
        limits=RuntimeLimits(
            cpu_count=1,
            memory_mb=256,
            disk_mb=512,
            timeout_seconds=10,
            max_output_bytes=3,
        ),
    )

    command = RuntimeManager(session, docker).execute_command(
        workspace_id=workspace.id,
        runtime=runtime,
        command=["python", "noisy.py"],
    )

    event = session.scalar(
        select(RuntimeEvent).where(
            RuntimeEvent.workspace_runtime_id == runtime.id,
            RuntimeEvent.event_type == "runtime.command.output_limited",
        )
    )
    security_event = session.scalar(
        select(SecurityEvent).where(SecurityEvent.action == "runtime.command.output_limited")
    )
    assert command.status == "completed"
    assert command.stdout == "abc"
    assert command.stderr == "xyz"
    assert event is not None
    assert event.event_metadata["max_output_bytes"] == 3
    assert event.event_metadata["stdout_truncated"] is True
    assert event.event_metadata["stderr_truncated"] is False
    assert security_event is not None
    assert security_event.outcome == "limited"
    assert security_event.event_metadata["command_id"] == str(command.id)


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
    cleanup_event = session.scalar(
        select(RuntimeEvent).where(
            RuntimeEvent.workspace_runtime_id == runtime.id,
            RuntimeEvent.event_type == "runtime.cleanup",
        )
    )
    assert cleanup_event is not None
    assert cleanup_event.event_metadata["cleanup"]["action"] == "stale_cleanup"
    assert cleanup_event.event_metadata["cleanup"]["success"] is True


def test_cleanup_stale_runtime_records_failure_evidence() -> None:
    class FailingDockerClient(FakeDockerClient):
        def remove_container(self, container_id: str) -> None:
            super().remove_container(container_id)
            raise RuntimeError("docker daemon unavailable")

    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="acme-fail", settings={})
    template = RuntimeTemplate(
        name="python",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.commit()
    docker = FailingDockerClient()
    runtime = RuntimeManager(session, docker).create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="analysis",
        limits=RuntimeLimits(cpu_count=1, memory_mb=256, disk_mb=512, timeout_seconds=10),
    )
    runtime.status = "failed"
    session.commit()

    RuntimeManager(session, docker).cleanup_stale_runtime(runtime)

    event = session.scalar(
        select(RuntimeEvent).where(
            RuntimeEvent.workspace_runtime_id == runtime.id,
            RuntimeEvent.event_type == "runtime.cleanup_failed",
        )
    )
    assert docker.removed == ["container-123"]
    assert runtime.status == "cleanup_failed"
    assert event is not None
    assert event.event_metadata["cleanup"]["success"] is False
    assert event.event_metadata["cleanup"]["error"] == "docker daemon unavailable"
    lease = session.scalar(
        select(RuntimeLease).where(RuntimeLease.workspace_runtime_id == runtime.id)
    )
    assert lease is not None
    assert lease.status == "cleanup_failed"
    assert lease.released_at is not None


def test_cleanup_stale_runtime_removes_managed_host_resources(tmp_path: Path) -> None:
    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="acme-host-cleanup", settings={})
    template = RuntimeTemplate(
        name="python",
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.commit()
    managed_root = tmp_path / "runtimes"
    temp_dir = managed_root / "runtime-1" / "tmp"
    staged_file = managed_root / "runtime-1" / "input.txt"
    temp_dir.mkdir(parents=True)
    staged_file.write_text("payload", encoding="utf-8")
    docker = FakeDockerClient()
    runtime = RuntimeManager(
        session,
        docker,
        managed_host_roots=[managed_root],
    ).create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="analysis",
        limits=RuntimeLimits(cpu_count=1, memory_mb=256, disk_mb=512, timeout_seconds=10),
    )
    runtime.status = "stopped"
    runtime.capabilities = {
        "managed_resources": {
            "temp_dirs": [str(temp_dir)],
            "staged_files": [str(staged_file)],
            "docker_volumes": ["opsmesh-runtime-1"],
        }
    }
    session.commit()

    RuntimeManager(
        session,
        docker,
        managed_host_roots=[managed_root],
    ).cleanup_stale_runtime(runtime)

    cleanup_event = session.scalar(
        select(RuntimeEvent).where(
            RuntimeEvent.workspace_runtime_id == runtime.id,
            RuntimeEvent.event_type == "runtime.cleanup",
        )
    )
    assert runtime.status == "deleted"
    assert not temp_dir.exists()
    assert not staged_file.exists()
    assert docker.removed_volumes == ["opsmesh-runtime-1"]
    assert cleanup_event is not None
    cleanup = cleanup_event.event_metadata["cleanup"]
    assert cleanup["success"] is True
    host_resources = cleanup["host_resources"]
    assert {item["status"] for item in host_resources} == {"deleted"}
    assert session.query(SecurityEvent).count() == 0


def test_cleanup_stale_runtime_rejects_unmanaged_host_resource(tmp_path: Path) -> None:
    session = _session()
    workspace = Workspace(
        owner_user_id=uuid4(),
        name="Acme",
        slug="acme-unsafe-cleanup",
        settings={},
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
    managed_root = tmp_path / "runtimes"
    unmanaged_file = tmp_path / "outside.txt"
    unmanaged_file.write_text("do not delete", encoding="utf-8")
    docker = FakeDockerClient()
    runtime = RuntimeManager(
        session,
        docker,
        managed_host_roots=[managed_root],
    ).create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="analysis",
        limits=RuntimeLimits(cpu_count=1, memory_mb=256, disk_mb=512, timeout_seconds=10),
    )
    runtime.status = "stopped"
    runtime.capabilities = {"managed_resources": {"staged_files": [str(unmanaged_file)]}}
    session.commit()

    RuntimeManager(
        session,
        docker,
        managed_host_roots=[managed_root],
    ).cleanup_stale_runtime(runtime)

    event = session.scalar(
        select(RuntimeEvent).where(
            RuntimeEvent.workspace_runtime_id == runtime.id,
            RuntimeEvent.event_type == "runtime.cleanup_failed",
        )
    )
    security_event = session.scalar(
        select(SecurityEvent).where(SecurityEvent.action == "runtime.cleanup.failed")
    )
    assert unmanaged_file.exists()
    assert runtime.status == "cleanup_failed"
    assert event is not None
    assert event.event_metadata["cleanup"]["success"] is False
    assert event.event_metadata["cleanup"]["host_resources"][0]["status"] == "unsafe_path"
    assert security_event is not None
    assert security_event.workspace_id == workspace.id
    assert security_event.severity == "critical"


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


def test_runtime_manager_rejects_process_limit_over_workspace_quota() -> None:
    session = _session()
    workspace = Workspace(
        owner_user_id=uuid4(),
        name="Acme",
        slug="acme-process-quota",
        settings={"runtime_quota": {"max_runtime_processes": 32}},
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
            name="too-many-processes",
            limits=RuntimeLimits(
                cpu_count=1,
                memory_mb=256,
                disk_mb=512,
                timeout_seconds=10,
                max_processes=64,
            ),
        )
    except RuntimeQuotaExceededError as exc:
        assert exc.code == "runtime_process_quota_exceeded"
    else:
        raise AssertionError("Expected runtime process quota to fail")

    assert docker.created_requests == []


def test_docker_cli_create_container_applies_disk_and_process_limits(monkeypatch) -> None:
    from backend.app.runtime_manager.docker_client import DockerCliRuntimeClient

    captured: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
        timeout: int,
    ):
        _ = capture_output, check, text, timeout
        captured.append(command)

        class Completed:
            returncode = 0
            stdout = "container-abc\n"
            stderr = ""

        return Completed()

    monkeypatch.setattr("subprocess.run", fake_run)

    container_id = DockerCliRuntimeClient().create_container(
        RuntimeCreateRequest(
            image="python:3.12-slim",
            name="opsmesh-test",
            workspace_id="workspace-1",
            runtime_id="runtime-1",
            limits=RuntimeLimits(
                cpu_count=1,
                memory_mb=512,
                disk_mb=2048,
                timeout_seconds=30,
                max_processes=96,
            ),
            mounts=(
                RuntimeMount(
                    source="opsmesh-ws-workspace-runtime",
                    target="/workspace",
                ),
            ),
            working_dir="/workspace",
        )
    )

    command = captured[0]
    assert container_id == "container-abc"
    assert command[command.index("--pids-limit") + 1] == "96"
    assert command[command.index("--storage-opt") + 1] == "size=2048m"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges:true"
    assert "--read-only" in command
    tmpfs_values = [
        value
        for index, value in enumerate(command)
        if index > 0 and command[index - 1] == "--tmpfs"
    ]
    assert tmpfs_values == [
        "/tmp:rw,noexec,nosuid,nodev,size=64m",
        "/var/tmp:rw,noexec,nosuid,nodev,size=16m",
    ]
    assert "--mount" in command
    assert command[command.index("--workdir") + 1] == "/workspace"
    assert "opsmesh.runtime_id=runtime-1" in command


def test_docker_cli_exec_streams_transient_stdin_without_argv_secret(monkeypatch) -> None:
    from backend.app.runtime_manager.docker_client import DockerCliRuntimeClient

    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        capture_output: bool,
        check: bool,
        input: str,
        text: bool,
        timeout: int,
    ):
        _ = capture_output, check, text, timeout
        captured["command"] = command
        captured["input"] = input

        class Completed:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return Completed()

    monkeypatch.setattr("subprocess.run", fake_run)

    result = DockerCliRuntimeClient().exec_command(
        "container-123",
        ["python", "-m", "worker", "--request-stdin"],
        10,
        stdin_data="runtime-secret",
    )

    assert result.exit_code == 0
    assert captured["command"] == [
        "docker",
        "exec",
        "-i",
        "container-123",
        "python",
        "-m",
        "worker",
        "--request-stdin",
    ]
    assert "runtime-secret" not in str(captured["command"])
    assert captured["input"] == "runtime-secret"


def test_runtime_control_service_applies_team_runtime_space_policy() -> None:
    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug="acme", settings={})
    template = RuntimeTemplate(
        name="python",
        image="python:3.12-slim",
        default_limits={
            "cpu_count": 4,
            "memory_mb": 4096,
            "disk_mb": 8192,
            "timeout_seconds": 300,
            "max_output_bytes": 900_000,
            "max_processes": 300,
        },
        default_network_policy={"allow_network": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Novel Studio",
        team_type="creative",
        default_task_policy={
            "runtime": {
                "network": {"disabled": True},
                "limits": {
                    "cpu_count": 1,
                    "memory_mb": 1024,
                    "timeout_seconds": 45,
                    "max_processes": 128,
                },
            }
        },
    )
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Novel Studio Space",
        scope="team",
        policy={
            "runtime": {
                "limits": {
                    "disk_mb": 2048,
                    "max_output_bytes": 120_000,
                }
            }
        },
        network_policy={"mode": "none"},
    )
    session.add_all([team, runtime_space])
    session.flush()
    session.add(
        RuntimeSpaceBinding(
            workspace_id=workspace.id,
            runtime_space_id=runtime_space.id,
            target_type="agent_team",
            target_id=team.id,
        )
    )
    session.commit()
    docker = FakeDockerClient()
    service = RuntimeControlService(
        session,
        settings=Settings(
            storage_root=".opsmesh-test-storage",
            runtime_allowed_images=["python:3.12-slim"],
        ),
        docker_client=docker,
    )

    runtime = service.create_runtime(
        workspace_id=workspace.id,
        template_id=template.id,
        name="drafting-runtime",
        limits=None,
        network_disabled=False,
        runtime_space_id=runtime_space.id,
    )

    assert runtime is not None
    request = docker.created_requests[0]
    assert request.network_disabled is True
    assert request.limits.cpu_count == 1
    assert request.limits.memory_mb == 1024
    assert request.limits.disk_mb == 2048
    assert request.limits.timeout_seconds == 45
    assert request.limits.max_output_bytes == 120_000
    assert request.limits.max_processes == 128
    assert request.labels["opsmesh.team_id"] == str(team.id)
    assert request.labels["opsmesh.runtime_space_scope"] == "team"

    policy_resolution = runtime.capabilities["policy_resolution"]
    assert policy_resolution["runtime_space"]["scope"] == "team"
    assert policy_resolution["team"]["id"] == str(team.id)
    assert policy_resolution["effective"]["network_disabled"] is True
    reduced_limits = {
        item["limit"]: item["effective"]
        for item in policy_resolution["limit_reductions"]
    }
    assert reduced_limits == {
        "cpu_count": 1,
        "memory_mb": 1024,
        "timeout_seconds": 45,
        "max_processes": 128,
        "disk_mb": 2048,
        "max_output_bytes": 120_000,
    }

    diagnostics = RuntimeSpaceService(session).diagnostics(workspace.id, runtime_space.id)
    assert diagnostics is not None
    assert diagnostics.runtimes[0].policy_resolution["team"]["id"] == str(team.id)


def _runtime_space_quotas(session: Session, runtime_space_id: object) -> dict[str, int]:
    return {
        quota.quota_key: quota.reserved_value
        for quota in session.scalars(
            select(RuntimeSpaceQuota).where(
                RuntimeSpaceQuota.runtime_space_id == runtime_space_id,
            )
        ).all()
    }


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
