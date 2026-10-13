"""Native SQLAlchemySession receipts, retries and tenant scope on PostgreSQL."""

import asyncio
import os
import selectors
import sys
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine
from temporalio.testing import ActivityEnvironment

from opsmesh.agents.execution.providers.openai.contracts import AgentSessionReference
from opsmesh.agents.sessions.activities import SessionActivities
from opsmesh.agents.sessions.contracts import SessionOperation
from opsmesh.agents.sessions.models import (
    AgentSessionMetadata,
    SDKSessionMessageRecord,
    SDKSessionOperationReceipt,
)
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.orchestration.runs.control import RunControlService
from opsmesh.orchestration.runs.models import AgentRun

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


def test_native_session_activity_retries_do_not_duplicate_or_pop_twice(native_execution):
    state = native_execution
    asyncio.run(state.activities.prepare(state.request))
    reference = AgentSessionReference(
        workspace_id=state.request.workspace_id,
        run_id=state.request.resource_id,
        session_id=f"{state.request.workspace_id}:receipt-validation",
    )
    with state.sessions() as session:
        run = session.get(AgentRun, reference.run_id)
        run.session_key = reference.session_id
        session.add(
            AgentSessionMetadata(
                workspace_id=reference.workspace_id,
                session_key=reference.session_id,
                scope_type="task_agent",
                scope_id=str(state.task_id),
                task_id=state.task_id,
                agent_profile_id=state.profile_id,
            )
        )
        session.commit()
        schema = session.scalar(select(func.current_schema()))

    async def scenario():
        engine = create_async_engine(
            state.sessions.kw["bind"].url,
            connect_args={"options": f"-csearch_path={schema},public"},
        )
        activities = SessionActivities(engine, state.sessions)
        environment = ActivityEnvironment()
        environment.info = replace(
            environment.info, workflow_id=state.request.workflow_id, activity_id="append"
        )
        messages = [
            {"role": "user", "content": "first"},
            {
                "type": "function_call",
                "call_id": "lookup-call",
                "name": "lookup",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "lookup-call",
                "output": "incident-code-42",
            },
            {"role": "assistant", "content": "second"},
        ]
        operation = SessionOperation(reference=reference, method="add_items", items=messages)
        try:
            await environment.run(activities.operate, operation)
            environment.info = replace(environment.info, attempt=2)
            await environment.run(activities.operate, operation)
            assert (
                await environment.run(
                    activities.operate, SessionOperation(reference=reference, method="get_items")
                )
                == messages
            )
            with state.sessions() as session:
                session.add(
                    SDKSessionMessageRecord(
                        session_id=reference.session_id, message_data="invalid-json"
                    )
                )
                session.commit()
            environment.info = replace(environment.info, activity_id="pop", attempt=1)
            pop = SessionOperation(reference=reference, method="pop_item")
            assert await environment.run(activities.operate, pop) == messages[-1]
            environment.info = replace(environment.info, attempt=2)
            assert await environment.run(activities.operate, pop) == messages[-1]
            assert (
                await environment.run(
                    activities.operate, SessionOperation(reference=reference, method="get_items")
                )
                == messages[:-1]
            )
            with pytest.raises(ValueError, match="different input"):
                await environment.run(activities.operate, operation)
            with pytest.raises(ResourceAccessDeniedError):
                await environment.run(
                    activities.operate,
                    SessionOperation(
                        reference=reference.model_copy(update={"workspace_id": uuid4()}),
                        method="get_items",
                    ),
                )
            with state.sessions() as session:
                assert (
                    session.scalar(select(func.count()).select_from(SDKSessionOperationReceipt))
                    == 2
                )
                assert session.scalar(
                    select(func.count()).select_from(SDKSessionMessageRecord)
                ) == (len(messages) - 1)
        finally:
            await activities.close()
            await engine.dispose()

    with asyncio.Runner(
        loop_factory=(lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
        if sys.platform == "win32"
        else None,
    ) as runner:
        runner.run(scenario())


@pytest.mark.parametrize("method", ["add_items", "pop_item", "clear_session"])
def test_native_session_rechecks_cancellation_inside_mutation_transaction(
    native_execution, monkeypatch, method
):
    state = native_execution
    asyncio.run(state.activities.prepare(state.request))
    reference = AgentSessionReference(
        workspace_id=state.request.workspace_id,
        run_id=state.request.resource_id,
        session_id=f"{state.request.workspace_id}:cancelled-session-validation",
    )
    with state.sessions() as session:
        run = session.get(AgentRun, reference.run_id)
        run.session_key = reference.session_id
        session.add(
            AgentSessionMetadata(
                workspace_id=reference.workspace_id,
                session_key=reference.session_id,
                scope_type="task_agent",
                scope_id=str(state.task_id),
                task_id=state.task_id,
                agent_profile_id=state.profile_id,
            )
        )
        session.commit()
        schema = session.scalar(select(func.current_schema()))

    async def scenario():
        engine = create_async_engine(
            state.sessions.kw["bind"].url,
            connect_args={"options": f"-csearch_path={schema},public"},
        )
        activities = SessionActivities(engine, state.sessions)
        environment = ActivityEnvironment()
        try:
            initial = [{"role": "user", "content": "committed before cancellation"}]
            await activities.execute(
                SessionOperation(reference=reference, method="add_items", items=initial),
                operation_id="initial-session-write",
            )
            authorize = activities._authorize

            def cancel_after_authorization(reference):
                authorize(reference)
                with state.sessions() as session:
                    RunControlService(session).cancel_run(
                        workspace_id=reference.workspace_id,
                        run_id=reference.run_id,
                        actor_user_id=state.user_id,
                    )

            monkeypatch.setattr(activities, "_authorize", cancel_after_authorization)
            operation = SessionOperation(
                reference=reference,
                method=method,
                items=[{"role": "assistant", "content": "must not commit"}]
                if method == "add_items"
                else [],
            )
            with pytest.raises(ResourceAccessDeniedError):
                await environment.run(activities.operate, operation)
            with state.sessions() as session:
                assert session.get(AgentRun, reference.run_id).status == "cancelled"
                rows = session.scalars(
                    select(SDKSessionMessageRecord.message_data).where(
                        SDKSessionMessageRecord.session_id == reference.session_id
                    )
                ).all()
                assert len(rows) == 1
                assert "committed before cancellation" in rows[0]
                assert "must not commit" not in rows[0]
                assert (
                    session.scalar(select(func.count()).select_from(SDKSessionOperationReceipt))
                    == 1
                )
        finally:
            await activities.close()
            await engine.dispose()

    asyncio.run(scenario())
