from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agents.models import AgentProfile
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.orchestration.scheduler import WorkspaceScheduler
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.status import TaskStatus
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_workspace_scheduler_orders_steps_by_task_priority_and_run_quota() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_active_runs": 1}},
    )
    low_task, low_step = _seed_task_step(session, workspace, title="Low", priority=1)
    high_task, high_step = _seed_task_step(session, workspace, title="High", priority=10)

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[low_step, high_step],
    )

    assert decision.available_run_slots == 1
    assert [step.task_id for step in decision.runnable_steps] == [high_task.id]
    assert [step.task_id for step in decision.blocked_steps] == [low_task.id]
    assert low_step.dependencies["blocked_reason"] == "workspace_run_quota_exceeded"
    assert high_step.dependencies == {}


def test_workspace_scheduler_round_robins_between_tasks_before_extra_parallel_steps() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_active_runs": 3}},
    )
    first_task, first_step = _seed_task_step(session, workspace, title="First", priority=10)
    _, first_extra_step = _seed_extra_step(session, first_task, title="First extra", order_index=1)
    second_task, second_step = _seed_task_step(session, workspace, title="Second", priority=8)
    third_task, third_step = _seed_task_step(session, workspace, title="Third", priority=7)

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[first_step, first_extra_step, second_step, third_step],
    )

    assert [step.task_id for step in decision.runnable_steps] == [
        first_task.id,
        second_task.id,
        third_task.id,
    ]
    assert decision.blocked_steps == (first_extra_step,)
    assert first_extra_step.dependencies["blocked_reason"] == "workspace_run_quota_exceeded"


def test_workspace_scheduler_can_allow_multiple_steps_per_task_per_tick() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={
            "scheduler": {
                "max_active_runs": 3,
                "max_steps_per_task_per_tick": 2,
            }
        },
    )
    first_task, first_step = _seed_task_step(session, workspace, title="First", priority=10)
    _, first_extra_step = _seed_extra_step(session, first_task, title="First extra", order_index=1)
    second_task, second_step = _seed_task_step(session, workspace, title="Second", priority=8)

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[first_step, first_extra_step, second_step],
    )

    assert [step.id for step in decision.runnable_steps] == [
        first_step.id,
        first_extra_step.id,
        second_step.id,
    ]
    assert decision.blocked_steps == ()


def test_workspace_scheduler_task_quota_allows_highest_priority_new_tasks() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_running_tasks": 1}},
    )
    low_task, low_step = _seed_task_step(session, workspace, title="Low", priority=1)
    high_task, high_step = _seed_task_step(session, workspace, title="High", priority=10)

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[low_step, high_step],
    )

    assert [step.task_id for step in decision.runnable_steps] == [high_task.id]
    assert decision.blocked_steps == (low_step,)
    assert decision.blocked_reason == "workspace_task_quota_exceeded"
    assert low_step.dependencies["blocked_reason"] == "workspace_task_quota_exceeded"


def test_workspace_scheduler_task_quota_keeps_existing_active_task_eligible() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_running_tasks": 1}},
    )
    active_task, active_next_step = _seed_task_step(
        session,
        workspace,
        title="Active",
        priority=1,
    )
    active_run = AgentRun(
        workspace_id=workspace.id,
        task_id=active_task.id,
        status=RunStatus.RUNNING.value,
    )
    high_task, high_step = _seed_task_step(session, workspace, title="High", priority=10)
    session.add(active_run)
    session.flush()

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[active_next_step, high_step],
    )

    assert decision.runnable_steps == (active_next_step,)
    assert decision.blocked_steps == (high_step,)
    assert high_step.dependencies["blocked_reason"] == "workspace_task_quota_exceeded"


def test_workspace_scheduler_blocks_when_active_run_quota_is_full() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_active_runs": 1}},
    )
    task, step = _seed_task_step(session, workspace, title="Blocked", priority=1)
    session.add(
        AgentRun(
            workspace_id=workspace.id,
            task_id=task.id,
            status=RunStatus.RUNNING.value,
        )
    )
    session.flush()

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[step],
    )

    assert decision.runnable_steps == ()
    assert decision.blocked_steps == (step,)
    assert decision.blocked_reason == "workspace_run_quota_exceeded"
    assert step.dependencies["scheduling_status"] == "blocked"


def test_run_orchestration_reserves_runtime_space_capacity_before_enqueue() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_active_runs": 10}},
    )
    runtime_space = _seed_runtime_space(session, workspace, active_runs=1)
    first_task, first_step = _seed_task_step(
        session,
        workspace,
        title="High",
        priority=10,
        runtime_space_id=runtime_space.id,
    )
    second_task, second_step = _seed_task_step(
        session,
        workspace,
        title="Low",
        priority=1,
        runtime_space_id=runtime_space.id,
    )

    runs = RunOrchestrationService(session).schedule_workspace_steps(
        workspace_id=workspace.id,
    )

    quota = _runtime_space_quota(session, runtime_space.id)
    reservations = _runtime_space_reservations(session, runtime_space.id)

    assert len(runs) == 1
    assert runs[0].task_id == first_task.id
    assert quota.reserved_value == 1
    assert len(reservations) == 1
    assert reservations[0].agent_run_id == runs[0].id
    assert reservations[0].status == "active"
    assert first_step.dependencies == {}
    assert second_step.dependencies["scheduling_status"] == "blocked"
    assert second_step.dependencies["blocked_reason"] == "runtime_space_quota_exceeded"

    RunOrchestrationService(session)._mark_run_cancelled(
        runs[0],
        completed_at=datetime.now(UTC),
    )
    next_runs = RunOrchestrationService(session).schedule_workspace_steps(
        workspace_id=workspace.id,
    )

    assert _runtime_space_quota(session, runtime_space.id).reserved_value == 1
    assert len(next_runs) == 1
    assert next_runs[0].task_id == second_task.id
    cancelled_step = session.get(TaskStep, first_step.id)
    assert cancelled_step is not None
    assert cancelled_step.status == "cancelled"
    released = [
        reservation
        for reservation in _runtime_space_reservations(session, runtime_space.id)
        if reservation.task_id == first_task.id
    ][0]
    assert released.status == "released"
    assert released.released_at is not None


def test_run_orchestration_reserves_runtime_space_resource_requirements() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_active_runs": 10}},
    )
    runtime_space = _seed_runtime_space(
        session,
        workspace,
        active_runs=10,
        quota_limits={"memory_mb": 4096, "cpu": 4},
        policy={"resource_requirements": {"memory_mb": 2048, "cpu": 1}},
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Image Worker",
        role="designer",
        runtime_policy={"resource_requirements": {"memory_mb": 4096, "cpu": 2}},
    )
    session.add(agent)
    session.flush()
    first_task, first_step = _seed_task_step(
        session,
        workspace,
        title="Render high",
        priority=10,
        runtime_space_id=runtime_space.id,
        assigned_agent_profile_id=agent.id,
        dependencies={"resource_requirements": {"cpu": 3}},
    )
    second_task, second_step = _seed_task_step(
        session,
        workspace,
        title="Render low",
        priority=1,
        runtime_space_id=runtime_space.id,
        assigned_agent_profile_id=agent.id,
    )

    runs = RunOrchestrationService(session).schedule_workspace_steps(workspace_id=workspace.id)

    memory_quota = _runtime_space_quota(session, runtime_space.id, "memory_mb")
    cpu_quota = _runtime_space_quota(session, runtime_space.id, "cpu")
    reservations = _runtime_space_reservations(session, runtime_space.id)

    assert len(runs) == 1
    assert runs[0].task_id == first_task.id
    assert memory_quota.reserved_value == 4096
    assert cpu_quota.reserved_value == 3
    assert reservations[0].resource_usage == {"active_runs": 1, "memory_mb": 4096, "cpu": 3}
    assert first_step.dependencies == {"resource_requirements": {"cpu": 3}}
    assert second_step.dependencies["blocked_reason"] == "runtime_space_quota_exceeded"

    RunOrchestrationService(session)._mark_run_cancelled(
        runs[0],
        completed_at=datetime.now(UTC),
    )
    next_runs = RunOrchestrationService(session).schedule_workspace_steps(
        workspace_id=workspace.id,
    )

    assert len(next_runs) == 1
    assert next_runs[0].task_id == second_task.id
    assert _runtime_space_quota(session, runtime_space.id, "memory_mb").reserved_value == 4096
    assert _runtime_space_quota(session, runtime_space.id, "cpu").reserved_value == 2


def _seed_task_step(
    session: Session,
    workspace: Workspace,
    *,
    title: str,
    priority: int,
    runtime_space_id: UUID | None = None,
    assigned_agent_profile_id: UUID | None = None,
    dependencies: dict[str, object] | None = None,
) -> tuple[Task, TaskStep]:
    task = Task(
        workspace_id=workspace.id,
        title=title,
        priority=priority,
        status=TaskStatus.QUEUED.value,
        runtime_space_id=runtime_space_id,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title=f"{title} step",
        status="queued",
        order_index=0,
        runtime_space_id=runtime_space_id,
        assigned_agent_profile_id=assigned_agent_profile_id,
        dependencies=dependencies or {},
    )
    session.add(step)
    session.flush()
    return task, step


def _seed_extra_step(
    session: Session,
    task: Task,
    *,
    title: str,
    order_index: int,
    dependencies: dict[str, object] | None = None,
) -> tuple[Task, TaskStep]:
    step = TaskStep(
        workspace_id=task.workspace_id,
        task_id=task.id,
        title=title,
        status="queued",
        order_index=order_index,
        runtime_space_id=task.runtime_space_id,
        dependencies=dependencies or {},
    )
    session.add(step)
    session.flush()
    return task, step


def _seed_runtime_space(
    session: Session,
    workspace: Workspace,
    *,
    active_runs: int,
    quota_limits: dict[str, int] | None = None,
    policy: dict[str, object] | None = None,
) -> RuntimeSpace:
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team space",
        scope="team",
        policy=policy or {},
    )
    session.add(runtime_space)
    session.flush()
    quotas = {"active_runs": active_runs} | (quota_limits or {})
    for quota_key, limit_value in quotas.items():
        session.add(
        RuntimeSpaceQuota(
            workspace_id=workspace.id,
            runtime_space_id=runtime_space.id,
                quota_key=quota_key,
                limit_value=limit_value,
            )
        )
    session.flush()
    return runtime_space


def _runtime_space_quota(
    session: Session,
    runtime_space_id: UUID,
    quota_key: str = "active_runs",
) -> RuntimeSpaceQuota:
    quota = session.scalar(
        select(RuntimeSpaceQuota).where(
            RuntimeSpaceQuota.runtime_space_id == runtime_space_id,
            RuntimeSpaceQuota.quota_key == quota_key,
        )
    )
    assert quota is not None
    return quota


def _runtime_space_reservations(
    session: Session,
    runtime_space_id: UUID,
) -> list[RuntimeSpaceReservation]:
    return list(
        session.scalars(
            select(RuntimeSpaceReservation)
            .where(RuntimeSpaceReservation.runtime_space_id == runtime_space_id)
            .order_by(RuntimeSpaceReservation.created_at.asc())
        ).all()
    )


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(
    session: Session,
    *,
    settings: dict[str, object] | None = None,
) -> tuple[User, Workspace]:
    user = User(email=f"{uuid4()}@example.com", display_name="Owner")
    workspace = Workspace(
        owner=user,
        name="Acme",
        slug=str(uuid4()),
        settings=settings or {},
    )
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
