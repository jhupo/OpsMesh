import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.orchestration.definitions.snapshot import node_definition_snapshot
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.release import release_execution
from opsmesh.orchestration.tasks.contracts import TaskControlActionRequest
from opsmesh.orchestration.tasks.control.service import TaskControlService
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.db.base import Base
from opsmesh.shared.errors import ConflictError
from tests.fixtures.database import temporary_postgres_schema
from tests.fixtures.temporal import seed_workspace

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


def test_pause_releases_execution_but_keeps_task_step_resumable() -> None:
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            user, workspace = seed_workspace(session)
            agent = AgentProfile(workspace_id=workspace.id, name="Assistant", role="assistant")
            session.add(agent)
            session.flush()
            task = Task(
                workspace_id=workspace.id,
                created_by_user_id=user.id,
                owner_agent_profile_id=agent.id,
                title="Pause",
                status="running",
            )
            session.add(task)
            session.flush()
            step = TaskStep(
                workspace_id=workspace.id,
                task_id=task.id,
                title="Work",
                assigned_agent_profile_id=agent.id,
                status="running",
                order_index=1,
            )
            session.add(step)
            session.flush()
            run = AgentRun(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_profile_id=agent.id,
                status="running",
                started_at=datetime.now(UTC),
                input={
                    "authorization_snapshot": {"node_definition": node_definition_snapshot(step)}
                },
            )
            session.add(run)
            session.commit()
            service = TaskControlService(session)
            paused = service.apply_action(
                workspace_id=workspace.id,
                task_id=task.id,
                actor_user_id=user.id,
                request=TaskControlActionRequest(action="pause"),
            )
            assert paused["status"] == "applied"
            assert step.status == "blocked"
            assert run.status == "cancelled"
            cancellations = list(
                session.scalars(
                    select(TemporalOutboxEntry).where(TemporalOutboxEntry.operation == "cancel")
                )
            )
            assert len(cancellations) == 1
            assert cancellations[0].workflow_id.endswith(f":agent:{run.id}")
            with pytest.raises(ConflictError, match="cancellation has not completed"):
                service.apply_action(
                    workspace_id=workspace.id,
                    task_id=task.id,
                    actor_user_id=user.id,
                    request=TaskControlActionRequest(action="resume", enqueue=False),
                )
            session.rollback()
            release_execution(session, workspace.id, run.id)
            session.commit()
            resumed = service.apply_action(
                workspace_id=workspace.id,
                task_id=task.id,
                actor_user_id=user.id,
                request=TaskControlActionRequest(action="resume", enqueue=False),
            )
            assert resumed["status"] == "applied"
            assert step.status == "queued"
            assert task.status == "running"
