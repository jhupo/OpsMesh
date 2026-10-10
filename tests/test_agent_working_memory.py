from uuid import uuid4

from sqlalchemy import JSON as SqliteJSON
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.resources.memory.models import WorkspaceMemoryEntry
from opsmesh.resources.memory.working import AgentWorkingMemoryService
from opsmesh.shared.db.base import Base
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember


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
    tool_entry = WorkspaceMemoryEntry(
        workspace_id=workspace.id,
        created_by_agent_profile_id=profile.id,
        created_by_agent_run_id=run.id,
        source_type="import",
        source_id=str(run.id),
        memory_layer="working",
        scope_type="run",
        scope_id=str(run.id),
        memory_key="note",
        entry_type="note",
        title="Imported note",
        content="Verify signed release",
        tags=[],
        visibility_scope="run",
        importance=0,
        status="active",
        revision=1,
        content_fingerprint="fixture",
        memory_metadata={},
    )
    session.add(tool_entry)
    session.flush()
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


def test_working_memory_promotion_rejects_cross_workspace_record() -> None:
    import pytest

    session = _session()
    with pytest.raises(ValueError):
        AgentWorkingMemoryService(session).promote(
            workspace_id=uuid4(),
            run_id=uuid4(),
            memory_entry_id=uuid4(),
        )


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
