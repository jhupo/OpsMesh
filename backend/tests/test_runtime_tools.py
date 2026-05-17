from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.approvals.models import Approval
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandResult,
    RuntimeCreateRequest,
    RuntimeLimits,
)
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtimes.models import RuntimeTemplate
from backend.app.tasks.models import Task
from backend.app.tasks.status import TaskStatus
from backend.app.tools.context import ToolContext
from backend.app.tools.runtime_tools import RuntimeToolService
from backend.app.workspaces.models import Workspace


class FakeDockerClient(DockerRuntimeClient):
    def __init__(self, result: RuntimeCommandResult | None = None) -> None:
        self.result = result or RuntimeCommandResult(exit_code=0, stdout="ok", stderr="")
        self.executed: list[list[str]] = []

    def create_container(self, request: RuntimeCreateRequest) -> str:
        return "container-123"

    def start_container(self, container_id: str) -> None:
        return None

    def stop_container(self, container_id: str) -> None:
        return None

    def remove_container(self, container_id: str) -> None:
        return None

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
    ) -> RuntimeCommandResult:
        self.executed.append(command)
        return self.result


def test_runtime_tool_executes_inside_runtime_manager() -> None:
    session = _session()
    workspace, template = _seed_runtime_template(session)
    docker = FakeDockerClient()
    manager = RuntimeManager(session, docker)
    runtime = manager.create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="runtime",
        limits=RuntimeLimits(cpu_count=1, memory_mb=256, disk_mb=512, timeout_seconds=10),
    )
    task = Task(workspace_id=workspace.id, title="Task")
    run = AgentRun(workspace_id=workspace.id, task_id=task.id)
    session.add_all([task, run])
    session.commit()
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        allowed_tools=frozenset({"runtime_shell"}),
    )

    result = RuntimeToolService(session, manager).execute_shell(
        context,
        runtime=runtime,
        command=["echo", "ok"],
    )

    assert result.status == "completed"
    assert result.stdout == "ok"
    assert docker.executed == [["echo", "ok"]]
    events = session.scalars(
        select(RunEvent).where(RunEvent.agent_run_id == run.id).order_by(RunEvent.sequence)
    ).all()
    assert [event.event_type for event in events] == ["tool.called", "tool.completed"]


def test_runtime_tool_blocks_risky_command_for_approval() -> None:
    session = _session()
    workspace, template = _seed_runtime_template(session)
    docker = FakeDockerClient()
    manager = RuntimeManager(session, docker)
    runtime = manager.create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="runtime",
        limits=RuntimeLimits(cpu_count=1, memory_mb=256, disk_mb=512, timeout_seconds=10),
    )
    task = Task(workspace_id=workspace.id, title="Task")
    run = AgentRun(workspace_id=workspace.id, task_id=task.id)
    session.add_all([task, run])
    session.commit()
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        allowed_tools=frozenset({"runtime_shell"}),
    )

    result = RuntimeToolService(session, manager).execute_shell(
        context,
        runtime=runtime,
        command=["rm", "-rf", "/workspace"],
    )

    assert result.approval_required is True
    assert result.status == "waiting_approval"
    assert docker.executed == []
    approval = session.scalars(select(Approval)).one()
    assert approval.task_id == task.id
    assert approval.agent_run_id == run.id
    assert approval.payload == {
        "command": ["rm", "-rf", "/workspace"],
        "runtime_id": str(runtime.id),
    }
    assert run.status == RunStatus.WAITING_APPROVAL.value
    assert task.status == TaskStatus.WAITING_APPROVAL.value
    events = session.scalars(
        select(RunEvent).where(RunEvent.agent_run_id == run.id).order_by(RunEvent.sequence)
    ).all()
    assert [event.event_type for event in events] == ["tool.called", "approval.requested"]


def test_runtime_tool_surfaces_command_failure() -> None:
    session = _session()
    workspace, template = _seed_runtime_template(session)
    docker = FakeDockerClient(RuntimeCommandResult(exit_code=2, stdout="", stderr="bad"))
    manager = RuntimeManager(session, docker)
    runtime = manager.create_runtime(
        workspace_id=workspace.id,
        template=template,
        name="runtime",
        limits=RuntimeLimits(cpu_count=1, memory_mb=256, disk_mb=512, timeout_seconds=10),
    )
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=None,
        agent_run_id=None,
        allowed_tools=frozenset({"runtime_shell"}),
    )

    result = RuntimeToolService(session, manager).execute_shell(
        context,
        runtime=runtime,
        command=["python", "missing.py"],
    )

    assert result.status == "failed"
    assert result.exit_code == 2
    assert result.stderr == "bad"


def _seed_runtime_template(session: Session) -> tuple[Workspace, RuntimeTemplate]:
    workspace = Workspace(owner_user_id=uuid4(), name="Acme", slug=str(uuid4()), settings={})
    template = RuntimeTemplate(
        name=str(uuid4()),
        image="python:3.12-slim",
        default_limits={},
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([workspace, template])
    session.commit()
    return workspace, template


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
