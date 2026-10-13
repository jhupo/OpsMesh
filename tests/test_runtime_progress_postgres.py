"""Official SDK lifecycle/stream evidence commits during model waits in PostgreSQL."""

import asyncio
import os
from uuid import uuid4

import pytest
from agents import Model
from sqlalchemy import func, select

from opsmesh.agents.execution.contracts import AgentLifecycleEvent
from opsmesh.agents.execution.providers.openai.workflow import (
    AgentLifecycleNotification,
    AgentWorkflow,
)
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.orchestration.tasks.models import TaskEventRecord
from tests.fixtures.native_agent_worker import (
    OfflineModelProvider,
    answer,
    native_agent_worker,
    stream_completed,
    stream_delta,
    stream_item,
)

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL") or os.getenv("OPSMESH_TEST_TEMPORAL") != "1",
    reason="Real PostgreSQL and Temporal required",
)


@pytest.mark.parametrize("stream", [False, True])
def test_sdk_progress_commits_before_model_finishes_and_replays_without_duplicates(
    native_execution, stream
):
    state = native_execution

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class WaitingModel(Model):
            async def get_response(self, **kwargs):
                entered.set()
                await release.wait()
                return answer("durable answer")

            async def stream_response(self, **kwargs):
                entered.set()
                yield stream_delta("durable live delta")
                await release.wait()
                yield stream_item("durable answer")
                yield stream_completed("durable answer")

        async with (
            native_agent_worker(
                state, OfflineModelProvider(lambda _: WaitingModel()), stream=stream
            ) as host,
            host.worker(),
        ):
            handle = await host.environment.client.start_workflow(
                AgentWorkflow.run,
                state.request,
                id=state.request.workflow_id,
                task_queue=host.task_queue,
            )
            try:
                await asyncio.wait_for(entered.wait(), 30)

                async def durable_progress():
                    while True:
                        with state.sessions() as session:
                            rows = session.scalars(
                                select(RunEvent).where(
                                    RunEvent.agent_run_id == state.request.resource_id
                                )
                            ).all()
                            if stream and "durable live delta" not in str(
                                [row.event_metadata for row in rows]
                            ):
                                await asyncio.sleep(0.05)
                                continue
                            run = session.get(AgentRun, state.request.resource_id)
                            assert run.status == "running"
                            assert {"agent.started", "agent.llm.started"}.issubset(
                                {row.event_type for row in rows}
                            )
                            assert not any(row.event_type == "agent.completed" for row in rows)
                            return

                await asyncio.wait_for(durable_progress(), 20)
                foreign = state.request.model_copy(update={"workspace_id": uuid4()})
                with pytest.raises(ValueError, match="workspace"):
                    await state.activities.lifecycle(
                        AgentLifecycleNotification(
                            request=foreign,
                            event_id=uuid4(),
                            event=AgentLifecycleEvent("agent.started", "Foreign"),
                        )
                    )
            finally:
                release.set()
            await asyncio.wait_for(handle.result(), 30)

        with state.sessions() as session:
            event = session.scalar(
                select(RunEvent).where(
                    RunEvent.agent_run_id == state.request.resource_id,
                    RunEvent.event_type == "agent.started",
                )
            )
            notification = AgentLifecycleNotification(
                request=state.request,
                event_id=event.id,
                event=AgentLifecycleEvent("agent.started", "Replay", payload={}),
            )
        await state.activities.lifecycle(notification)
        await state.activities.lifecycle(notification)

    asyncio.run(scenario())
    with state.sessions() as session:
        run = session.get(AgentRun, state.request.resource_id)
        assert run.status == "completed"
        lifecycle = session.scalars(
            select(RunEvent.event_type).where(
                RunEvent.agent_run_id == run.id,
                RunEvent.event_type.in_(
                    ["agent.started", "agent.llm.started", "agent.llm.completed", "agent.completed"]
                ),
            )
        ).all()
        assert sorted(lifecycle) == sorted(
            ["agent.started", "agent.llm.started", "agent.llm.completed", "agent.completed"]
        )
        if stream:
            records = session.scalars(
                select(TaskEventRecord).where(TaskEventRecord.task_id == state.task_id)
            ).all()
            assert "durable live delta" in str([row.payload for row in records])
            assert len(records) == len({row.id for row in records})
        assert session.scalar(select(func.count()).select_from(RunEvent)) > 0
