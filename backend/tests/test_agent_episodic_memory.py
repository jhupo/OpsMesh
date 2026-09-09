from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.types import JSON

from backend.app.agent_runtime.contracts import AgentRunResult
from backend.app.agents.memory_policy import normalized_memory_policy
from backend.app.agents.models import AgentProfile
from backend.app.approvals.models import Approval
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.memory.episodic import AgentEpisodicMemoryService
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.runs.models import AgentRun
from backend.app.tasks.feedback import TaskFeedbackService
from backend.app.tasks.models import Task
from backend.app.tools.workspace_memory import WorkspaceMemorySearchService
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_memory_policy_rejects_removed_root_fields() -> None:
    with pytest.raises(ValueError, match="Invalid agent memory policy"):
        normalized_memory_policy({"auto_capture": True})


def test_run_outcomes_are_idempotent_and_include_task_run_provenance() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    profile = AgentProfile(
        workspace_id=workspace.id,
        name="Operator",
        role="operator",
        instructions="Operate safely.",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Repair deployment",
        status="running",
    )
    session.add_all([profile, task])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=profile.id,
        status="completed",
        input={},
        completed_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    service = AgentEpisodicMemoryService(session)

    first = service.capture_run_completed(
        run,
        AgentRunResult(final_output="Deployment repaired."),
    )
    repeated = service.capture_run_completed(
        run,
        AgentRunResult(final_output="This duplicate is ignored."),
    )

    assert first is repeated
    assert first is not None
    assert first.memory_layer == "episodic"
    assert first.scope_type == "task"
    assert first.scope_id == str(task.id)
    assert first.memory_metadata["task"]["id"] == str(task.id)
    assert first.memory_metadata["run"]["id"] == str(run.id)
    assert first.expires_at is not None


def test_decisions_failures_and_human_feedback_create_distinct_episodes() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    profile = AgentProfile(
        workspace_id=workspace.id,
        name="Operator",
        role="operator",
        instructions="Operate safely.",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Repair deployment",
        status="running",
    )
    session.add_all([profile, task])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=profile.id,
        status="failed",
        input={},
        completed_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    approval = Approval(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        requested_by_agent_profile_id=profile.id,
        approval_type="tool_execution",
        risk_level="high",
        payload={},
        status="rejected",
        decided_by_user_id=user.id,
        decision_reason="Unsafe target token=hidden-reason",
        created_at=datetime.now(UTC),
        decided_at=datetime.now(UTC),
    )
    session.add(approval)
    session.flush()
    service = AgentEpisodicMemoryService(session)
    service.capture_run_failed(
        run,
        {"code": "command_failed", "message": "token=hidden-error", "retryable": True},
    )
    service.capture_approval_decision(approval, actor_user_id=user.id)
    feedback = TaskFeedbackService(session).record(
        workspace_id=workspace.id,
        task_id=task.id,
        actor_user_id=user.id,
        body="Keep the rollback steps explicit token=hidden-feedback",
        feedback_kind="correction",
        metadata={},
    )
    assert feedback is not None

    entries = session.scalars(
        select(WorkspaceMemoryEntry).order_by(WorkspaceMemoryEntry.entry_type)
    ).all()
    assert {entry.entry_type for entry in entries} == {
        "agent_run_failed",
        "approval_decision",
        "task_human_feedback",
    }
    serialized = " ".join(entry.content for entry in entries)
    assert "hidden-error" not in serialized
    assert "hidden-reason" not in serialized
    assert "hidden-feedback" not in serialized
    assert all(entry.memory_metadata["task"]["id"] == str(task.id) for entry in entries)
    hits = WorkspaceMemorySearchService(session).search(
        workspace_id=workspace.id,
        query="command_failed",
        limit=5,
        source_types={"workspace_memory"},
    )
    assert hits[0]["metadata"]["task"]["id"] == str(task.id)
    assert hits[0]["metadata"]["run"]["id"] == str(run.id)


def _session() -> Session:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = JSON()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email="owner@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug="acme", settings={})
    session.add_all(
        [user, workspace, WorkspaceMember(workspace=workspace, user=user, role="owner")]
    )
    session.commit()
    return user, workspace
