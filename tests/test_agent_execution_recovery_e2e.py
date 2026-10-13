"""Product replay, cancellation and live authorization against real PostgreSQL."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from opsmesh.agents.execution.contracts import AgentRunResult
from opsmesh.agents.execution.providers.openai.workflow import AgentExecutionOutcome
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun, RunEvent

pytest_plugins = ["tests.fixtures.native_agent_execution"]

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


def test_duplicate_native_result_is_terminally_idempotent(native_execution):
    state = native_execution
    definition = asyncio.run(state.activities.prepare(state.request))
    assert definition is not None
    outcome = AgentExecutionOutcome(
        request=state.request,
        definition=definition,
        result=AgentRunResult(final_output="completed-once"),
    )
    asyncio.run(state.activities.complete(outcome))
    asyncio.run(state.activities.complete(outcome))
    assert asyncio.run(state.activities.prepare(state.request)) is None
    with state.sessions() as session:
        run = session.get(AgentRun, state.request.resource_id)
        assert run.status == "completed"
        assert (
            session.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(RunEvent.agent_run_id == run.id, RunEvent.event_type == "run.completed")
            )
            == 1
        )


def test_cancel_is_idempotent_and_late_native_result_is_discarded(native_execution):
    state = native_execution
    definition = asyncio.run(state.activities.prepare(state.request))
    assert definition is not None
    asyncio.run(state.activities.cancel(state.request))
    asyncio.run(state.activities.cancel(state.request))
    asyncio.run(
        state.activities.complete(
            AgentExecutionOutcome(
                request=state.request,
                definition=definition,
                result=AgentRunResult(final_output="must-not-publish"),
            )
        )
    )
    with state.sessions() as session:
        run = session.get(AgentRun, state.request.resource_id)
        assert run.status == "cancelled"
        assert "must-not-publish" not in str(run.output)
        assert (
            session.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(
                    RunEvent.agent_run_id == run.id,
                    RunEvent.event_type == "run.cancellation_propagated",
                )
            )
            == 1
        )


def test_live_principal_revocation_discards_native_result(native_execution):
    state = native_execution
    definition = asyncio.run(state.activities.prepare(state.request))
    assert definition is not None
    with state.sessions() as session:
        session.get(User, state.user_id).status = "disabled"
        session.commit()
    asyncio.run(
        state.activities.complete(
            AgentExecutionOutcome(
                request=state.request,
                definition=definition,
                result=AgentRunResult(final_output="must-not-publish"),
            )
        )
    )
    with state.sessions() as session:
        run = session.get(AgentRun, state.request.resource_id)
        assert run.status == "failed"
        assert "must-not-publish" not in str(run.output)
        assert (
            session.scalar(
                select(RunEvent).where(
                    RunEvent.agent_run_id == run.id,
                    RunEvent.event_type == "authorization.revoked",
                )
            )
            is not None
        )


def test_activity_rejects_cross_workspace_reference(native_execution):
    state = native_execution
    foreign = state.request.model_copy(update={"workspace_id": uuid4()})
    with pytest.raises((ResourceAccessDeniedError, ValueError)):
        asyncio.run(state.activities.prepare(foreign))
    with state.sessions() as session:
        assert session.get(AgentRun, state.request.resource_id).status == "queued"
