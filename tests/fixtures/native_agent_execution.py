"""Real PostgreSQL fixture for the product side of native SDK Activities."""

from dataclasses import dataclass
from uuid import UUID

import pytest
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.agents.execution.providers.openai.activities import AgentExecutionActivities
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.orchestration.runs.service import RunOrchestrationService
from opsmesh.orchestration.tasks.models import Task
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from tests.fixtures.agent_product import _seed_workspace
from tests.fixtures.database import temporary_postgres_schema


@dataclass
class NativeAgentExecution:
    sessions: sessionmaker[Session]
    activities: AgentExecutionActivities
    request: ExecutionRequest
    user_id: UUID
    task_id: UUID
    profile_id: UUID


@pytest.fixture
def native_execution(tmp_path):
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        settings = Settings(environment="test", storage_root=str(tmp_path / "artifacts"))
        with sessions() as session:
            user, workspace = _seed_workspace(session)
            profile = AgentProfile(
                workspace_id=workspace.id,
                name="Expert",
                role="expert",
                instructions="Use the authorized tools and answer",
                model="gpt-4.1",
                tool_policy={"allowed_tools": ["write_artifact", "read_workspace_file"]},
            )
            session.add(profile)
            session.flush()
            task = Task(
                workspace_id=workspace.id,
                created_by_user_id=user.id,
                owner_agent_profile_id=profile.id,
                title="Native execution validation",
                execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
                status="queued",
            )
            session.add(task)
            session.flush()
            run = RunOrchestrationService(session).create_queued_run_for_task(task)
            assert run is not None
            session.commit()
            request = ExecutionRequest(
                execution_id=run.id,
                workspace_id=workspace.id,
                resource_id=run.id,
                kind=ExecutionKind.AGENT,
                requested_by_user_id=user.id,
                trace_id="0123456789abcdef0123456789abcdef",
                request_id="native-execution-request",
            )
            yield NativeAgentExecution(
                sessions,
                AgentExecutionActivities(sessions, settings),
                request,
                user.id,
                task.id,
                profile.id,
            )
