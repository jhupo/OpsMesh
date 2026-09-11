from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.admin.models import PlatformPolicy
from backend.app.admin.risky_policy_values import RISKY_EXECUTION_POLICY_KEY
from backend.app.approvals.models import Approval
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.reviews.models import ResourceReview
from backend.app.reviews.service import ResourcePolicyReviewBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandInputFile,
    RuntimeCommandResult,
    RuntimeCreateRequest,
    RuntimeLimits,
)
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtime_manager.models import RuntimeTemplate
from backend.app.tasks.models import Task
from backend.app.tasks.status import TaskStatus
from backend.app.tools.context import ToolContext
from backend.app.tools.runtime_tools import RuntimeToolService
from backend.app.workspaces.models import Workspace


@pytest.fixture(autouse=True)
def _approve_semantic_runtime_tool_review(monkeypatch: pytest.MonkeyPatch) -> None:
    def approved_review(self: ResourcePolicyReviewBuilder, **_: object) -> ResourceReview:
        return ResourceReview(
            required=False,
            risk_level="low",
            reasons=["llm_review.approved"],
            signals={"reviewer": "codex-auto-review"},
        )

    monkeypatch.setattr(
        ResourcePolicyReviewBuilder,
        "review_tool_execution",
        approved_review,
    )


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

    def remove_volume(self, volume_name: str) -> None:
        return None

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
        *,
        input_file: RuntimeCommandInputFile | None = None,
    ) -> RuntimeCommandResult:
        _ = input_file
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
    task = Task(workspace_id=workspace.id, title="Task", status=TaskStatus.RUNNING.value)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        status=RunStatus.RUNNING.value,
    )
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
    task = Task(workspace_id=workspace.id, title="Task", status=TaskStatus.RUNNING.value)
    session.add(task)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        status=RunStatus.RUNNING.value,
    )
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
    assert approval.payload["command_preview"] == ["rm", "-rf", "/workspace"]
    assert approval.payload["runtime_id"] == str(runtime.id)
    assert approval.payload["execution_review"]["risk_level"] == "high"
    assert "tool.policy.risk_level.high" in approval.payload["execution_review"]["reasons"]
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


def test_runtime_tool_blocks_when_platform_policy_disables_commands() -> None:
    session = _session()
    workspace, template = _seed_runtime_template(session)
    _seed_risky_policy(session, allow_runtime_commands=False)
    docker = FakeDockerClient()
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
        command=["echo", "blocked"],
    )

    assert result.status == "blocked"
    assert "disabled" in (result.reason or "")
    assert docker.executed == []


def test_runtime_tool_review_requires_approval_even_when_platform_gate_allows() -> None:
    session = _session()
    workspace, template = _seed_runtime_template(session)
    _seed_risky_policy(session, high_risk_tool_mode="allow")
    docker = FakeDockerClient()
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
        command=["rm", "-rf", "/workspace"],
    )

    approval = session.scalar(select(Approval))

    assert result.status == "waiting_approval"
    assert docker.executed == []
    assert approval is not None
    assert approval.payload["execution_review"]["risk_level"] == "high"


def test_runtime_tool_blocks_high_risk_when_platform_policy_blocks_it() -> None:
    session = _session()
    workspace, template = _seed_runtime_template(session)
    _seed_risky_policy(session, high_risk_tool_mode="block")
    docker = FakeDockerClient()
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
        command=["rm", "-rf", "/workspace"],
    )

    assert result.status == "blocked"
    assert "High-risk" in (result.reason or "")
    assert docker.executed == []


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


def _seed_risky_policy(
    session: Session,
    *,
    allow_runtime_commands: bool = True,
    allow_network_egress: bool = False,
    allow_self_hosted_runtimes: bool = True,
    require_approval_for_high_risk_tools: bool | None = None,
    high_risk_tool_mode: str = "require_workspace_approval",
) -> PlatformPolicy:
    approval_required = (
        high_risk_tool_mode == "require_workspace_approval"
        if require_approval_for_high_risk_tools is None
        else require_approval_for_high_risk_tools
    )
    policy = PlatformPolicy(
        policy_key=RISKY_EXECUTION_POLICY_KEY,
        value={
            "allow_runtime_commands": allow_runtime_commands,
            "allow_network_egress": allow_network_egress,
            "allow_self_hosted_runtimes": allow_self_hosted_runtimes,
            "require_approval_for_high_risk_tools": approval_required,
            "high_risk_tool_mode": high_risk_tool_mode,
        },
        description="test",
    )
    session.add(policy)
    session.commit()
    return policy
