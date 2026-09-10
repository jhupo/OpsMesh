import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.app.agents.models import AgentProfile
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.planning.attempts import TaskPlanningAttemptService
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam
from backend.app.workspaces.models import Workspace
from backend.tests.test_postgres_scheduler_concurrency import _temporary_postgres_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL integration URL required"
)


def test_concurrent_initial_planning_creates_one_attempt() -> None:
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Owner")
            workspace = Workspace(owner=user, name="Planning", slug=uuid4().hex)
            session.add_all([user, workspace])
            session.flush()
            agent = AgentProfile(workspace_id=workspace.id, name="Planner", role="manager")
            session.add(agent)
            session.flush()
            team = AgentTeam(
                workspace_id=workspace.id, name="Team", manager_agent_profile_id=agent.id
            )
            session.add(team)
            session.flush()
            task = Task(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                title="Report",
                team_snapshot={
                    "team": {"manager_agent_profile_id": str(agent.id)},
                    "members": [],
                    "agents": [{"id": str(agent.id), "role": "manager"}],
                },
            )
            session.add(task)
            session.commit()
            workspace_id, task_id = workspace.id, task.id
        barrier = threading.Barrier(2)

        def plan():
            with factory() as session:
                task = session.scalar(
                    select(Task).where(
                        Task.workspace_id == workspace_id,
                        Task.id == task_id,
                    )
                )
                assert task is not None
                barrier.wait(timeout=10)
                result = TaskPlanningAttemptService(session).ensure_initial_plan(task)
                session.commit()
                assert result is not None
                return result["plan_id"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(plan) for _ in range(2)]
            ids = [future.result(timeout=20) for future in futures]
        with factory() as session:
            assert (
                session.scalar(
                    select(func.count(TaskPlanningAttempt.id)).where(
                        TaskPlanningAttempt.workspace_id == workspace_id,
                        TaskPlanningAttempt.task_id == task_id,
                    )
                )
                == 1
            )
        assert ids[0] == ids[1]
