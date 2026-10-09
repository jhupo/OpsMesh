from uuid import uuid4

from sqlalchemy import JSON as SqliteJSON
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import sessionmaker

from backend.app.agents.profiles.models import AgentProfile
from backend.app.identity.users.models import User
from backend.app.orchestration.runs.models import AgentRun
from backend.app.orchestration.tasks.models import Task, TaskStep
from backend.app.resources.memory.policy import WorkingMemoryPolicy
from backend.app.resources.memory.working import AgentWorkingMemoryService
from backend.app.shared.db.base import Base
from backend.app.workspaces.management.models import Workspace
from backend.app.workspaces.members.models import WorkspaceMember


def test_working_memory_is_run_scoped_versioned_redacted_and_expirable() -> None:
    session = _session()
    user = User(email="owner@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug="acme", settings={})
    session.add_all(
        [user, workspace, WorkspaceMember(workspace=workspace, user=user, role="owner")]
    )
    session.flush()
    profile = AgentProfile(
        workspace_id=workspace.id,
        name="Builder",
        role="worker",
        instructions="Build safely.",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Ship release token=objective-secret",
        description="Prepare the final package.",
        project_plan={"steps": ["build", "verify"]},
    )
    session.add_all([profile, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Build",
        description="Package",
        acceptance_criteria=["Tests pass", "token=criteria-secret"],
        expected_artifacts=["package.whl", "password=artifact-secret"],
    )
    session.add(step)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=profile.id,
        status="running",
        input={},
    )
    other_run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=profile.id,
        status="running",
        input={},
    )
    session.add_all([run, other_run])
    session.flush()
    service = AgentWorkingMemoryService(session)
    policy = WorkingMemoryPolicy()

    def put(content):
        return service.put(
            run=run,
            profile=profile,
            key="note",
            title="Explicit note",
            content=content,
            entry_type="note",
            metadata={},
            session_key="session-one",
            policy=policy,
        )

    put("Verify release token=objective-secret")
    tool_entry = put("Verify signed release token=tool-secret")
    assert tool_entry.revision == 2
    assert "tool-secret" not in tool_entry.content
    assert len(service.active_for_run(workspace_id=workspace.id, run_id=run.id)) == 1
    assert service.active_for_run(workspace_id=workspace.id, run_id=other_run.id) == []
    promoted = service.promote(
        workspace_id=workspace.id,
        run_id=run.id,
        memory_entry_id=tool_entry.id,
    )
    replay = service.promote(
        workspace_id=workspace.id,
        run_id=run.id,
        memory_entry_id=tool_entry.id,
    )
    assert replay.id == promoted.id
    assert promoted.memory_layer == "episodic"
    assert promoted.scope_type == "task"
    assert tool_entry.status == "promoted"

    assert service.expire_run(workspace_id=workspace.id, run_id=run.id) == 0
    assert service.active_for_run(workspace_id=workspace.id, run_id=run.id) == []


def test_working_memory_rejects_cross_workspace_profile_binding() -> None:
    session = _session()
    user = User(email="isolation@example.com", display_name="Owner")
    first = Workspace(owner=user, name="First", slug=f"first-{uuid4()}", settings={})
    second = Workspace(owner=user, name="Second", slug=f"second-{uuid4()}", settings={})
    session.add_all([user, first, second])
    session.flush()
    profile = AgentProfile(workspace_id=second.id, name="Agent", role="worker")
    run = AgentRun(workspace_id=first.id, status="running", input={})
    session.add_all([profile, run])
    session.flush()

    try:
        AgentWorkingMemoryService(session).put(
            run=run,
            profile=profile,
            key="fact",
            title="Fact",
            content="value",
            entry_type="fact",
            metadata={},
            session_key=None,
            policy=WorkingMemoryPolicy(),
        )
    except ValueError as exc:
        assert str(exc) == "Working memory profile workspace mismatch"
    else:
        raise AssertionError("cross-workspace working memory must fail")


def _session():
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()
