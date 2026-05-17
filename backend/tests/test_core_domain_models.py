from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agents.models import AgentProfile
from backend.app.audit.models import AuditEvent
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus, can_transition_run, require_run_transition
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus, can_transition_task, require_task_transition
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_core_domain_round_trip() -> None:
    session = _session()
    user = User(email="owner@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug="acme", settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.flush()

    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Researcher",
        role="researcher",
        description="",
        instructions="Research market data",
    )
    team = AgentTeam(workspace_id=workspace.id, name="Market Team", team_type="research")
    team_member = AgentTeamMember(
        workspace_id=workspace.id,
        agent_team=team,
        agent_profile=agent,
        team_role="specialist",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Q2 Market Analysis",
    )
    step = TaskStep(workspace_id=workspace.id, task=task, title="Collect sources")
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=agent.id,
    )
    session.add_all(
        [
            agent,
            team,
            team_member,
            task,
            step,
            run,
        ]
    )
    session.flush()

    event = RunEvent(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        event_type="run.started",
        sequence=1,
        created_at=datetime.now(UTC),
    )
    audit = AuditEvent(
        workspace_id=workspace.id,
        actor_type="user",
        actor_id=str(user.id),
        user_id=user.id,
        action="task.create",
        target_type="task",
        target_id=str(task.id),
        created_at=datetime.now(UTC),
    )
    session.add_all([event, audit])
    session.commit()

    assert session.query(AgentProfile).count() == 1
    assert session.query(AgentTeam).count() == 1
    assert session.query(Task).count() == 1
    assert session.query(AgentRun).count() == 1
    assert session.query(RunEvent).count() == 1
    assert session.query(AuditEvent).count() == 1


def test_workspace_owned_tables_have_workspace_id() -> None:
    workspace_owned_models = [
        AgentProfile,
        AgentTeam,
        AgentTeamMember,
        Task,
        TaskStep,
        AgentRun,
        RunEvent,
        AuditEvent,
    ]

    for model in workspace_owned_models:
        assert "workspace_id" in model.__table__.c


def test_task_status_transitions() -> None:
    assert can_transition_task(TaskStatus.DRAFT, TaskStatus.QUEUED)
    assert can_transition_task(TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
    assert not can_transition_task(TaskStatus.COMPLETED, TaskStatus.RUNNING)

    require_task_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)


def test_run_status_transitions() -> None:
    assert can_transition_run(RunStatus.QUEUED, RunStatus.RUNNING)
    assert can_transition_run(RunStatus.RUNNING, RunStatus.COMPLETED)
    assert not can_transition_run(RunStatus.COMPLETED, RunStatus.RUNNING)

    require_run_transition(RunStatus.WAITING_APPROVAL, RunStatus.RUNNING)


def test_invalid_status_transition_raises() -> None:
    try:
        require_task_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING)
    except ValueError as exc:
        assert "Invalid task transition" in str(exc)
    else:
        raise AssertionError("Expected invalid task transition to raise")


def test_task_state_service_updates_completion_metadata() -> None:
    task = Task(workspace_id=uuid4(), title="Task")
    task.status = TaskStatus.RUNNING.value
    completed_at = datetime.now(UTC)

    transition = TaskStateService().transition(
        task,
        TaskStatus.COMPLETED,
        completed_at=completed_at,
        final_output={"result": "done"},
    )

    assert transition.previous_status == TaskStatus.RUNNING
    assert transition.next_status == TaskStatus.COMPLETED
    assert transition.changed is True
    assert task.status == TaskStatus.COMPLETED.value
    assert task.completed_at == completed_at
    assert task.final_output == {"result": "done"}


def test_task_state_service_reopens_failed_task_for_retry() -> None:
    task = Task(workspace_id=uuid4(), title="Task")
    task.status = TaskStatus.FAILED.value
    task.completed_at = datetime.now(UTC)
    task.final_output = {"error": "old"}

    TaskStateService().transition(task, TaskStatus.QUEUED)

    assert task.status == TaskStatus.QUEUED.value
    assert task.completed_at is None
    assert task.final_output is None


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
