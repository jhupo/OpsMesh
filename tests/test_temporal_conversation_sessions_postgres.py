"""Real Temporal SDK execution reuses conversation history on PostgreSQL."""

import asyncio
import os
import selectors
import sys
from datetime import timedelta

import pytest
from agents import Model, ModelProvider
from agents.items import TResponseInputItem
from openai.types.responses import Response, ResponseCompletedEvent, ResponseTextDeltaEvent
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine
from temporalio.openai_agents import ModelActivityParameters, OpenAIAgentsPlugin
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from opsmesh.agents.execution.providers.openai.stream_events import MODEL_EVENT_TOPIC
from opsmesh.agents.execution.providers.openai.workflow import AgentWorkflow
from opsmesh.agents.sessions.activities import SessionActivities
from opsmesh.agents.sessions.models import SDKSessionMessageRecord
from opsmesh.identity.authorization.context import AuthenticatedUser, WorkspaceContext
from opsmesh.identity.users.models import User
from opsmesh.orchestration.conversations.advancement import ConversationAdvanceService
from opsmesh.orchestration.conversations.models import ConversationExecution, ConversationTurn
from opsmesh.orchestration.conversations.schemas import ConversationCreateRequest
from opsmesh.orchestration.conversations.service import ConversationService
from opsmesh.orchestration.runs.activities import RunExecutionActivities
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.converter import sdk_data_converter
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL") or os.getenv("OPSMESH_TEST_TEMPORAL") != "1",
    reason="Real PostgreSQL and Temporal opt-in required",
)


def test_two_turns_use_native_history_and_other_conversation_starts_empty(native_execution):
    state = native_execution
    with state.sessions() as session:
        context = WorkspaceContext(
            AuthenticatedUser.from_model(session.get(User, state.user_id)),
            session.get(Workspace, state.request.workspace_id),
            session.scalar(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == state.request.workspace_id,
                    WorkspaceMember.user_id == state.user_id,
                )
            ),
        )
        service = ConversationService(session)
        conversation = service.create(
            context, ConversationCreateRequest(mode="agent", agent_profile_id=state.profile_id)
        )
        first = service.send(context, conversation.id, "Lookup the incident", "first")
        second = service.send(context, conversation.id, "Explain that result", "second")
        other = service.create(
            context, ConversationCreateRequest(mode="agent", agent_profile_id=state.profile_id)
        )
        third = service.send(context, other.id, "Independent question", "third")
        turns = [(conversation.id, first.id), (conversation.id, second.id), (other.id, third.id)]
        schema = session.scalar(select(func.current_schema()))

    seen = []

    class OfflineModel(Model):
        async def get_response(self, **kwargs):
            raise AssertionError("The native chat path must stream")

        async def stream_response(self, **kwargs):
            seen.append(
                TypeAdapter(list[TResponseInputItem]).dump_python(kwargs["input"], mode="json")
            )
            text = "incident-code-42" if len(seen) == 1 else "answered"
            yield ResponseTextDeltaEvent(
                type="response.output_text.delta",
                delta=text,
                content_index=0,
                output_index=0,
                sequence_number=0,
                item_id=f"message-{len(seen)}",
                logprobs=[],
            )
            response = Response.model_validate(
                {
                    "id": f"response-{len(seen)}",
                    "created_at": 0,
                    "model": "gpt-4.1",
                    "object": "response",
                    "status": "completed",
                    "parallel_tool_calls": False,
                    "tool_choice": "auto",
                    "tools": [],
                    "output": [
                        {
                            "id": f"message-{len(seen)}",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": text, "annotations": []}],
                        }
                    ],
                }
            )
            yield ResponseCompletedEvent(
                type="response.completed", sequence_number=1, response=response
            )

    class Provider(ModelProvider):
        def get_model(self, name):
            return OfflineModel()

    async def scenario():
        from opsmesh.agents.execution.providers.openai.stream_events import AgentStreamActivities

        engine = create_async_engine(
            state.sessions.kw["bind"].url,
            connect_args={"options": f"-csearch_path={schema},public"},
        )
        sessions = SessionActivities(engine, state.sessions)
        stream = AgentStreamActivities(state.sessions)
        activities = state.activities
        environment = await WorkflowEnvironment.start_local(data_converter=sdk_data_converter)
        keys = []
        try:
            async with Worker(
                environment.client,
                task_queue="conversation-native-history-test",
                workflows=[AgentWorkflow],
                activities=[
                    RunExecutionActivities(state.sessions).release,
                    activities.prepare,
                    activities.complete,
                    activities.lifecycle,
                    activities.authorize,
                    activities.fail,
                    activities.cancel,
                    sessions.operate,
                    stream.relay,
                ],
                plugins=[
                    OpenAIAgentsPlugin(
                        model_provider=Provider(),
                        model_params=ModelActivityParameters(
                            start_to_close_timeout=timedelta(seconds=30),
                            streaming_topic=MODEL_EVENT_TOPIC,
                        ),
                    )
                ],
            ):
                for conversation_id, turn_id in turns:
                    with state.sessions.begin() as session:
                        ConversationAdvanceService(session).advance(
                            state.request.workspace_id, conversation_id
                        )
                        task_id = session.scalar(
                            select(ConversationExecution.task_id).where(
                                ConversationExecution.workspace_id == state.request.workspace_id,
                                ConversationExecution.turn_id == turn_id,
                            )
                        )
                        run_id = session.scalar(
                            select(AgentRun.id).where(
                                AgentRun.workspace_id == state.request.workspace_id,
                                AgentRun.task_id == task_id,
                            )
                        )
                    assert run_id is not None
                    request = ExecutionRequest(
                        execution_id=run_id,
                        workspace_id=state.request.workspace_id,
                        resource_id=run_id,
                        kind=ExecutionKind.AGENT,
                        requested_by_user_id=state.user_id,
                    )
                    await asyncio.wait_for(
                        environment.client.execute_workflow(
                            AgentWorkflow.run,
                            request,
                            id=request.workflow_id,
                            task_queue="conversation-native-history-test",
                        ),
                        timeout=60,
                    )
                    with state.sessions.begin() as session:
                        keys.append(session.get(AgentRun, run_id).session_key)
                        history = session.scalars(
                            select(SDKSessionMessageRecord.message_data).where(
                                SDKSessionMessageRecord.session_id == keys[-1]
                            )
                        ).all()
                        if len(keys) == 1:
                            assert "incident-code-42" in str(history), history
                        ConversationAdvanceService(session).advance(
                            state.request.workspace_id, conversation_id
                        )
                        assert session.get(ConversationTurn, turn_id).status == "completed"
            assert keys[0] == keys[1] and keys[2] != keys[0]
        finally:
            await environment.shutdown()
            await sessions.close()
            await engine.dispose()

    with asyncio.Runner(
        loop_factory=(lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
        if sys.platform == "win32"
        else None,
    ) as runner:
        runner.run(scenario())
    assert len(seen) == 3
    assert "Lookup the incident" in str(seen[1])
    assert "incident-code-42" in str(seen[1]), seen[1]
    assert "Explain that result" in str(seen[1])
    assert "Lookup the incident" not in str(seen[2])
    assert "incident-code-42" not in str(seen[2])
