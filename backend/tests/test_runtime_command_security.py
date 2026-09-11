from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.audit.models import AuditEvent
from backend.app.core.config import Settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.runtime_manager.contracts import (
    RuntimeCommandInputFile,
    RuntimeCommandResult,
    RuntimeCreateRequest,
)
from backend.app.runtime_manager.service import RuntimeControlService
from backend.app.runtime_manager.models import RuntimeEvent, RuntimeTemplate, WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.workspaces.models import Workspace


class FakeDockerClient:
    def create_container(self, request: RuntimeCreateRequest) -> str:
        _ = request
        return "container"

    def start_container(self, container_id: str) -> None:
        _ = container_id

    def stop_container(self, container_id: str) -> None:
        _ = container_id

    def remove_container(self, container_id: str) -> None:
        _ = container_id

    def remove_volume(self, volume_name: str) -> None:
        _ = volume_name

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
        *,
        input_file: RuntimeCommandInputFile | None = None,
        working_dir: str | None = None,
    ) -> RuntimeCommandResult:
        _ = (container_id, command, timeout_seconds, input_file, working_dir)
        return RuntimeCommandResult(exit_code=0, stdout="ok", stderr="")

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


def test_risky_queued_command_is_blocked_and_audited_without_raw_command() -> None:
    session, runtime = _runtime()
    secret = "token-secret-value"
    command = RuntimeControlService(
        session,
        settings=Settings(environment="test"),
        docker_client=FakeDockerClient(),
    ).queue_command(
        workspace_id=runtime.workspace_id,
        runtime_id=runtime.id,
        command=["echo", secret],
    )

    assert command.status == "blocked"
    assert command.error == "runtime command denied by approval policy"
    events = session.scalars(select(RuntimeEvent)).all()
    assert len(events) == 1
    assert events[0].event_type == "runtime.command.blocked"
    assert secret not in events[0].message
    assert secret not in str(events[0].event_metadata)
    assert session.scalars(select(SecurityEvent)).one().action == "runtime.command.blocked"
    assert session.scalars(select(AuditEvent)).one().action == "runtime.command.blocked"


def test_queued_command_payload_is_immutable_at_execution() -> None:
    session, runtime = _runtime()
    docker = FakeDockerClient()
    service = RuntimeControlService(
        session,
        settings=Settings(environment="test"),
        docker_client=docker,
    )
    command = service.queue_command(
        workspace_id=runtime.workspace_id,
        runtime_id=runtime.id,
        command=["echo", "safe"],
    )
    assert command.status == "queued"
    result = service.execute_queued_command(
        workspace_id=runtime.workspace_id,
        runtime_id=runtime.id,
        command_id=command.id,
        command=["echo", "tampered"],
    )

    assert result is not None
    assert result.status == "blocked"
    assert result.error == "runtime command payload does not match the queued command"


def _runtime() -> tuple[Session, WorkspaceRuntime]:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug=str(uuid4()), settings={})
    template = RuntimeTemplate(
        name=str(uuid4()),
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_template_id=template.id,
        name="runtime",
        status="active",
        connection_status="online",
        docker_container_id="container",
        limits={"timeout_seconds": 10},
        network_policy={"mode": "none", "disabled": True},
    )
    session.add(runtime)
    session.commit()
    return session, runtime


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
