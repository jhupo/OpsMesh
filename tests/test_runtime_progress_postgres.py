"""SDK progress crosses Runtime RPC and commits before a long model call completes."""

import asyncio
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.execution.contracts import (
    AgentRunRequest,
    AgentRuntimeContext,
    AgentRuntimeEvent,
)
from opsmesh.agents.execution.providers.openai.runner import OpenAIAgentsRunner
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.orchestration.conversations.advancement import ConversationAdvanceService
from opsmesh.orchestration.conversations.service import ConversationService
from opsmesh.orchestration.runs.async_execution import AsyncAgentRunExecutor
from opsmesh.orchestration.runs.live_async import buffered_live_events
from opsmesh.orchestration.runs.live_events import RunLivePublisher
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.orchestration.runs.runtime_event_messages import RunRuntimeEventMessageMapper
from opsmesh.orchestration.tasks.events import RedisTaskEventBus
from opsmesh.runtime.agent_host.broker import RuntimeCallbackBroker
from opsmesh.runtime.agent_host.wire import LIFECYCLE_EVENT
from opsmesh.runtime.queues.contracts import JobPayload, JobType
from opsmesh.shared.concurrency import BlockingIO
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from opsmesh.shared.db.operations import DatabaseOperations
from tests.test_conversations_postgres import _context, _queue, _seed_conversation
from tests.test_postgres_scheduler_concurrency import _temporary_postgres_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL URL required"
)


def test_sdk_progress_is_durable_during_model_wait_and_not_replayed(monkeypatch):
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        wid, uid, mid, cid = _seed_conversation(factory)
        queue = _queue()
        with factory() as session:
            ConversationService(session).send(
                _context(session, wid, uid, mid), cid, "Inspect", "progress", queue=queue
            )
            ConversationAdvanceService(session).advance(wid, cid)
            run = session.scalar(select(AgentRun))
            run.status = "running"
            profile = session.get(AgentProfile, run.agent_profile_id)
            run_id, task_id = run.id, run.task_id
            session.commit()
        executor = AsyncAgentRunExecutor(factory, queue, Settings(environment="test"))
        bus = RedisTaskEventBus(queue.redis)
        job = JobPayload(
            workspace_id=wid,
            resource_id=run_id,
            requested_by_user_id=uid,
            job_type=JobType.AGENT_RUN,
            idempotency_key="progress",
        )

        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()

            class WaitingModel(Model):
                async def get_response(self, **kwargs):
                    entered.set()
                    await release.wait()
                    return ModelResponse(
                        output=[
                            ResponseOutputMessage(
                                id="offline",
                                type="message",
                                role="assistant",
                                status="completed",
                                content=[
                                    ResponseOutputText(
                                        type="output_text", text="OK", annotations=[]
                                    )
                                ],
                            )
                        ],
                        usage=Usage(requests=1),
                        response_id="offline",
                    )

                async def stream_response(self, **kwargs):
                    raise AssertionError("Lifecycle delivery must not require text streaming")
                    yield

            monkeypatch.setattr(
                "opsmesh.agents.execution.providers.openai.runner.OpenAIProvider.get_model",
                lambda *args, **kwargs: WaitingModel(),
            )
            with BlockingIO(2, name="progress-test") as io:
                controls = DatabaseOperations(factory, io, lambda: None)
                publisher = RunLivePublisher(bus, wid, task_id, run_id, None, None)
                request = executor._lifecycle_request(
                    AgentRunRequest(
                        profile,
                        "Inspect",
                        AgentRuntimeContext(wid, task_id, run_id),
                        api_key="offline",
                        event_sink=publisher,
                    ),
                    controls,
                    job,
                )
                broker = RuntimeCallbackBroker(request)

                async def rpc(event):
                    payload = LIFECYCLE_EVENT.dump_python(event, mode="json")
                    payload["message"] = "api_key=sk-progress-secret"
                    payload["payload"]["token"] = "secret"
                    await broker.dispatch("event.lifecycle", payload)

                async with buffered_live_events(request, io):
                    task = asyncio.create_task(
                        OpenAIAgentsRunner().run(replace(request, lifecycle_sink=rpc))
                    )
                    try:
                        await asyncio.wait_for(entered.wait(), 10)
                        assert not task.done()
                        with factory() as session:
                            events = session.scalars(
                                select(RunEvent)
                                .where(
                                    RunEvent.agent_run_id == run_id,
                                    RunEvent.event_type.like("agent.%"),
                                )
                                .order_by(RunEvent.sequence)
                            ).all()
                            assert [e.event_type for e in events] == [
                                "agent.started",
                                "agent.llm.started",
                            ]
                            assert all("sk-progress-secret" not in e.message for e in events)
                            assert all(
                                e.event_metadata["runtime_event"]["token"] == "[redacted]"
                                for e in events
                            )
                        # A Runtime cannot forge final control-plane evidence.
                        with pytest.raises(ValueError, match="control-plane"):
                            await broker.dispatch(
                                "event.lifecycle",
                                {"event_type": "run.completed", "message": "forged", "payload": {}},
                            )
                        # A callback's workspace/run identity is fixed by its owning Run.
                        foreign = job.model_copy(update={"workspace_id": uuid4()})
                        foreign_request = executor._lifecycle_request(request, controls, foreign)
                        with pytest.raises(ValueError, match="workspace"):
                            await foreign_request.lifecycle_sink(
                                AgentRuntimeEvent("agent.started", "Inspect")
                            )
                    finally:
                        release.set()
                    result = await asyncio.wait_for(task, 10)
                assert not any(e.event_type.startswith("agent.") for e in result.events)
                with factory() as session:
                    run = session.get(AgentRun, run_id)
                    RunRuntimeEventMessageMapper(session).map(run, result)
                    session.commit()
                    events = session.scalars(
                        select(RunEvent)
                        .where(RunEvent.agent_run_id == run_id, RunEvent.event_type.like("agent.%"))
                        .order_by(RunEvent.sequence)
                    ).all()
                    assert [e.event_type for e in events] == [
                        "agent.started",
                        "agent.llm.started",
                        "agent.llm.completed",
                        "agent.completed",
                    ]
                live = bus.read(workspace_id=wid, task_id=task_id, after_id="0-0", count=20)
                assert [e.payload["kind"] for e in live] == [
                    "started",
                    "llm.started",
                    "llm.completed",
                    "completed",
                ]
                assert all("sk-progress-secret" not in str(e.payload) for e in live)

        asyncio.run(scenario())
