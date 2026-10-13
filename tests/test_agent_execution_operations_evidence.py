"""Native SDK result evidence shares identity and is idempotent in PostgreSQL."""

import asyncio
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select
from temporalio import activity
from temporalio.testing import ActivityEnvironment

from opsmesh.agents.execution.contracts import (
    AgentModelUsageRecord,
    AgentReference,
    AgentRunResult,
    AgentStreamEvent,
    AgentUsageTotals,
)
from opsmesh.agents.execution.providers.openai.stream_events import AgentStreamActivities
from opsmesh.agents.execution.providers.openai.workflow import AgentExecutionOutcome
from opsmesh.governance.costs.models import ModelUsageRecord
from opsmesh.orchestration.runs.models import RunEvent
from opsmesh.orchestration.tasks.models import TaskEventRecord
from tests.fixtures.log_context import log_context

pytest_plugins = ["tests.fixtures.native_agent_execution"]

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


def test_native_sdk_result_evidence_shares_trace_and_run_identity(native_execution):
    state = native_execution
    with log_context(trace_id=state.request.trace_id, request_id=state.request.request_id):
        definition = asyncio.run(state.activities.prepare(state.request))
        assert definition is not None
        result = AgentRunResult(
            final_output="native answer",
            model_usages=(
                AgentModelUsageRecord(
                    request_id="provider-response",
                    agent=AgentReference("Expert", state.profile_id),
                    model="gpt-4.1",
                    provider="openai",
                    model_api="openai_responses",
                    usage=AgentUsageTotals(
                        request_count=1, input_tokens=10, output_tokens=5, total_tokens=15
                    ),
                ),
            ),
        )
        outcome = AgentExecutionOutcome(request=state.request, definition=definition, result=result)
        asyncio.run(state.activities.complete(outcome))
        asyncio.run(state.activities.complete(outcome))
    with state.sessions() as session:
        events = session.scalars(
            select(RunEvent).where(
                RunEvent.workspace_id == state.request.workspace_id,
                RunEvent.agent_run_id == state.request.resource_id,
            )
        ).all()
        records = session.scalars(
            select(ModelUsageRecord).where(
                ModelUsageRecord.workspace_id == state.request.workspace_id,
                ModelUsageRecord.agent_run_id == state.request.resource_id,
            )
        ).all()
        assert {item.trace_id for item in events} == {state.request.trace_id}
        assert len(records) == 1
        record = records[0]
        assert record.trace_id == state.request.trace_id
        assert record.task_id == state.task_id
        assert record.agent_profile_id == state.profile_id
        assert record.total_tokens == 15
        assert record.metering_status == "unpriced"
        assert len([item for item in events if item.event_type == "model_provider.used"]) == 1


def test_live_stream_replay_is_durable_scoped_and_redacted(native_execution):
    state = native_execution
    asyncio.run(state.activities.prepare(state.request))
    streams = AgentStreamActivities(state.sessions)
    delta = AgentStreamEvent(
        sequence=1,
        event_type="output.text.delta",
        payload={"api_key": "must-never-be-stored"},
        delta="Live answer",
    )

    @activity.defn
    async def persist(request):
        await asyncio.to_thread(streams._persist, request, 0, delta)

    async def scenario():
        environment = ActivityEnvironment()
        environment.info = replace(environment.info, workflow_id=state.request.workflow_id)
        await environment.run(persist, state.request)
        environment.info = replace(environment.info, attempt=2)
        await environment.run(persist, state.request)
        with pytest.raises(ValueError, match="workspace"):
            await environment.run(
                persist, state.request.model_copy(update={"workspace_id": uuid4()})
            )

    asyncio.run(scenario())
    with state.sessions() as session:
        events = session.scalars(
            select(RunEvent).where(
                RunEvent.workspace_id == state.request.workspace_id,
                RunEvent.agent_run_id == state.request.resource_id,
                RunEvent.event_type == "agent.stream.output.text.delta",
            )
        ).all()
        task_events = session.scalars(
            select(TaskEventRecord).where(
                TaskEventRecord.workspace_id == state.request.workspace_id,
                TaskEventRecord.task_id == state.task_id,
                TaskEventRecord.event_type == "agent.stream.output.text.delta",
            )
        ).all()
        assert len(events) == len(task_events) == 1
        assert task_events[0].event_id == events[0].id
        assert task_events[0].payload["runtime_stream_event"]["delta"] == "Live answer"
        assert "must-never-be-stored" not in str(events[0].event_metadata)
        assert "must-never-be-stored" not in str(task_events[0].payload)
