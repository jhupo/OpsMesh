from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.orchestration.scheduler import WorkspaceScheduler
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
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


def _seed_task_step(
    session: Session,
    workspace: Workspace,
    *,
    title: str,
    priority: int,
) -> tuple[Task, TaskStep]:
    task = Task(
        workspace_id=workspace.id,
        title=title,
        priority=priority,
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title=f"{title} step",
        status="queued",
        order_index=0,
    )
    session.add(step)
    session.flush()
    return task, step


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
