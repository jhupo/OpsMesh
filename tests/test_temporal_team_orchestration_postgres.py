"""Event-driven Team advancement, native request idempotency and revocation in PG."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from temporalio.exceptions import ApplicationError

from opsmesh.identity.users.models import User
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.config import Settings
from opsmesh.teams.execution.activities import TeamOrchestrationActivities
from opsmesh.teams.execution.dispatch import submit_team_orchestration
from opsmesh.teams.execution.models import TeamOrchestrationRequest
from opsmesh.teams.management.models import AgentTeam
from opsmesh.teams.sessions.schemas import AgentTeamOrchestrationAdvanceRequest

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


def _stage(state):
    command_id = uuid4()
    with state.sessions() as session:
        team = AgentTeam(
            workspace_id=state.request.workspace_id,
            name="Event team",
            manager_agent_profile_id=state.profile_id,
            default_task_policy={"team_runtime": {"status": "paused"}},
        )
        session.add(team)
        session.flush()
        kwargs = dict(
            workspace_id=state.request.workspace_id,
            team_id=team.id,
            user_id=state.user_id,
            request_id=command_id,
            configuration=AgentTeamOrchestrationAdvanceRequest(dry_run=False),
        )
        command, workflow_id = submit_team_orchestration(session, **kwargs)
        session.commit()
        submit_team_orchestration(session, **kwargs)
        session.commit()
        return ExecutionRequest(
            execution_id=command.id,
            workspace_id=command.workspace_id,
            resource_id=command.id,
            kind=ExecutionKind.TEAM_ORCHESTRATION,
            requested_by_user_id=state.user_id,
        )


def test_parallel_duplicate_team_request_advances_once(native_execution):
    state = native_execution
    request = _stage(state)
    activity = TeamOrchestrationActivities(
        ActivityDependencies(state.sessions, Settings(environment="test"))
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(lambda: asyncio.run(activity.advance(request))) for _ in range(2)]
        for job in jobs:
            job.result(timeout=30)
    with state.sessions() as session:
        command = session.get(TeamOrchestrationRequest, request.resource_id)
        assert command.status == "completed" and command.result["status"] == "skipped"
        team = session.get(AgentTeam, command.team_id)
        assert team.default_task_policy["team_runtime"]["advancement_count"] == 1
        assert "iteration_count" not in team.default_task_policy["team_runtime"]
        assert (
            session.scalar(
                select(func.count())
                .select_from(TemporalOutboxEntry)
                .where(
                    TemporalOutboxEntry.workflow_id == request.workflow_id,
                    TemporalOutboxEntry.operation == "start",
                )
            )
            == 1
        )


def test_queued_team_request_rechecks_principal_and_records_rejection(native_execution):
    state = native_execution
    request = _stage(state)
    with state.sessions() as session:
        session.get(User, state.user_id).status = "disabled"
        session.commit()
    activity = TeamOrchestrationActivities(
        ActivityDependencies(state.sessions, Settings(environment="test"))
    )
    with pytest.raises(ApplicationError):
        asyncio.run(activity.advance(request))
    with state.sessions() as session:
        command = session.get(TeamOrchestrationRequest, request.resource_id)
        assert command.status == "failed" and command.completed_at is not None
        assert command.started_at is None


def test_team_metadata_migration_replaces_loop_keys_and_downgrades(native_execution):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    state = native_execution
    request = _stage(state)
    with state.sessions() as session:
        command = session.get(TeamOrchestrationRequest, request.resource_id)
        team = session.get(AgentTeam, command.team_id)
        team_id = team.id
        team.default_task_policy = {
            "team_runtime": {
                "status": "paused",
                "iteration_count": 3,
                "last_iteration": {"iteration": 3, "status": "noop"},
                "last_heartbeat_at": "old",
                "stall_count": 9,
            }
        }
        session.commit()
    spec = importlib.util.spec_from_file_location(
        "team_migration", Path("migrations/versions/0126_team_orchestration_requests.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with (
        state.sessions.kw["bind"].begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        connection.execute(text("DELETE FROM team_orchestration_requests"))
        # Recreate the preceding schema so the migration runs its own table DDL.
        connection.execute(text("DROP TABLE team_orchestration_requests"))
        module.upgrade()
        value = connection.scalar(
            text("SELECT default_task_policy FROM agent_teams WHERE id=:id"), {"id": team_id}
        )
        metadata = value["team_runtime"]
        assert (
            metadata["advancement_count"] == 3 and metadata["last_advancement"]["advancement"] == 3
        )
        assert "iteration_count" not in metadata and "last_heartbeat_at" not in metadata
        assert "stall_count" not in metadata
        module.downgrade()
        value = connection.scalar(
            text("SELECT default_task_policy FROM agent_teams WHERE id=:id"), {"id": team_id}
        )
        assert value["team_runtime"]["iteration_count"] == 3
        assert value["team_runtime"]["last_iteration"]["iteration"] == 3


@pytest.mark.skipif(os.getenv("OPSMESH_TEST_TEMPORAL") != "1", reason="Temporal required")
def test_partial_team_effect_is_unknown_until_audited_human_confirmation(
    native_execution, monkeypatch
):
    from temporalio.client import WorkflowFailureError
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from opsmesh.identity.authorization.context import AuthenticatedUser
    from opsmesh.runtime.temporal.converter import sdk_data_converter
    from opsmesh.teams.execution.reconciliation import (
        TeamReconciliationConflictError,
        TeamReconciliationService,
    )
    from opsmesh.teams.execution.service import TeamOrchestrationService
    from opsmesh.teams.execution.workflow import TeamOrchestrationWorkflow
    from opsmesh.teams.sessions.schemas import AgentTeamOrchestrationReconcileRequest

    state = native_execution
    request = _stage(state)
    calls = []
    original_advance = TeamOrchestrationService.advance

    def interrupted_advance(self, **kwargs):
        calls.append(True)
        original_advance(self, **kwargs)
        self._session.commit()
        raise RuntimeError("Worker response lost after product commit")

    monkeypatch.setattr(TeamOrchestrationService, "advance", interrupted_advance)
    activities = TeamOrchestrationActivities(
        ActivityDependencies(state.sessions, Settings(environment="test"))
    )

    async def scenario():
        environment = await WorkflowEnvironment.start_local(data_converter=sdk_data_converter)
        try:
            async with Worker(
                environment.client,
                task_queue="team-outcome-reconciliation",
                workflows=[TeamOrchestrationWorkflow],
                activities=[activities.advance, activities.record_unknown],
            ):
                with pytest.raises(WorkflowFailureError):
                    await environment.client.execute_workflow(
                        TeamOrchestrationWorkflow.run,
                        request,
                        id=request.workflow_id,
                        task_queue="team-outcome-reconciliation",
                    )
        finally:
            await environment.shutdown()

    asyncio.run(scenario())
    assert len(calls) == 1
    with state.sessions() as session:
        command = session.get(TeamOrchestrationRequest, request.resource_id)
        assert command.status == "unknown"
        team = session.get(AgentTeam, command.team_id)
        assert team.default_task_policy["team_runtime"]["advancement_count"] == 1
        with pytest.raises(TeamReconciliationConflictError):
            submit_team_orchestration(
                session,
                workspace_id=request.workspace_id,
                team_id=team.id,
                user_id=state.user_id,
                configuration=AgentTeamOrchestrationAdvanceRequest(dry_run=False),
            )
        TeamReconciliationService(session).reconcile(
            workspace_id=request.workspace_id,
            team_id=team.id,
            request_id=command.id,
            user=AuthenticatedUser.from_model(session.get(User, state.user_id)),
            decision=AgentTeamOrchestrationReconcileRequest(
                status="completed",
                reason="Confirmed advancement_count and events",
                evidence={"advancement_count": 1},
            ),
        )
        session.commit()
    asyncio.run(activities.advance(request))
    assert len(calls) == 1
