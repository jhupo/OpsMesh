from uuid import uuid4

from sqlalchemy import JSON as SqliteJSON
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import sessionmaker

from backend.app.agent_runtime.contracts import AgentRuntimeToolResult
from backend.app.agents.memory_policy import WorkingMemoryPolicy
from backend.app.agents.models import AgentProfile
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.memory.working import AgentWorkingMemoryService, working_memory_context
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task
from backend.app.workspaces.models import Workspace, WorkspaceMember


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
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
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

    prepared = service.prepare_run(
        run=run,
        profile=profile,
        task=task,
        session_key="session-one",
        policy=policy,
    )
    task.description = "Prepare the signed final package."
    updated = service.prepare_run(
        run=run,
        profile=profile,
        task=task,
        session_key="session-one",
        policy=policy,
    )
    tool_entry = service.record_tool_result(
        context_workspace_id=workspace.id,
        run_id=run.id,
        tool_name="build",
        tool_call_id="call-1",
        result=AgentRuntimeToolResult(
            status="completed",
            output={"summary": "done", "api_key": "tool-secret"},
        ),
        policy=policy,
    )

    assert len(prepared) == 2
    assert len(updated) == 2
    assert updated[0].revision == 2
    assert "objective-secret" not in updated[0].content
    assert tool_entry is not None
    assert "tool-secret" not in tool_entry.content
    assert len(service.active_for_run(workspace_id=workspace.id, run_id=run.id)) == 3
    assert service.active_for_run(workspace_id=workspace.id, run_id=other_run.id) == []
    assert "Current run working memory:" in working_memory_context(updated)

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

    assert service.expire_run(workspace_id=workspace.id, run_id=run.id) == 2
    assert service.active_for_run(workspace_id=workspace.id, run_id=run.id) == []
    assert all(entry.status == "expired" for entry in updated)


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
