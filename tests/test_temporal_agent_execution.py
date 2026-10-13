"""Real Temporal + official SDK plugin, with offline model and product Activities.

These tests prove SDK integration and live stream timing, not PostgreSQL durability.
"""

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from agents import Model, ModelProvider
from openai.types.responses import Response, ResponseCompletedEvent, ResponseTextDeltaEvent
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.openai_agents import ModelActivityParameters, OpenAIAgentsPlugin
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from opsmesh.agents.execution.contracts import AgentExecutionContext, AgentProfileSnapshot
from opsmesh.agents.execution.providers.openai.contracts import (
    AgentExecutionDefinition,
    AgentModelReference,
)
from opsmesh.agents.execution.providers.openai.stream_events import (
    MODEL_EVENT_TOPIC,
    AgentStreamActivities,
)
from opsmesh.agents.execution.providers.openai.tools_temporal import AgentToolOperation
from opsmesh.agents.execution.providers.openai.workflow import (
    AgentExecutionFailure,
    AgentExecutionOutcome,
    AgentLifecycleNotification,
    AgentWorkflow,
)
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.converter import sdk_data_converter
from tests.fixtures.native_agent_worker import answer
from tests.fixtures.persistent_agent import PersistentAgentWorkflow

pytestmark = pytest.mark.skipif(
    os.getenv("OPSMESH_TEST_TEMPORAL") != "1", reason="Temporal integration opt-in required"
)


def test_native_sdk_completed_workflow_replays_without_repeating_model_activity():
    async def scenario():
        calls = []

        class CountingModel(Model):
            async def get_response(self, **kwargs):
                assert activity.in_activity()
                calls.append("durable-answer")
                return answer("durable-answer")

            async def stream_response(self, **kwargs):
                raise AssertionError("This scenario uses the native non-streaming Model Activity")
                yield

        class Provider(ModelProvider):
            def get_model(self, name):
                assert name == "offline"
                return CountingModel()

        plugin = OpenAIAgentsPlugin(
            model_provider=Provider(),
            model_params=ModelActivityParameters(start_to_close_timeout=timedelta(seconds=60)),
        )
        async with await WorkflowEnvironment.start_local(
            data_converter=sdk_data_converter
        ) as environment:
            async with Worker(
                environment.client,
                task_queue="native-sdk-replay-validation",
                workflows=[PersistentAgentWorkflow],
                plugins=[plugin],
            ):
                handle = await environment.client.start_workflow(
                    PersistentAgentWorkflow.run,
                    id=f"native-sdk-replay:{uuid4()}",
                    task_queue="native-sdk-replay-validation",
                )
                async with asyncio.timeout(30):
                    while await handle.query(PersistentAgentWorkflow.answer) is None:
                        await asyncio.sleep(0.05)
                await handle.signal(PersistentAgentWorkflow.release)
                assert await handle.result() == "durable-answer"
                history = await handle.fetch_history()
            await Replayer(
                workflows=[PersistentAgentWorkflow],
                plugins=[plugin],
                data_converter=sdk_data_converter,
            ).replay_workflow(history)
            assert calls == ["durable-answer"]

    asyncio.run(scenario())


@activity.defn(name="opsmesh.execution.release")
async def release_execution_boundary(request: ExecutionRequest) -> None:
    """Product Activity boundary for tests with in-memory product projections."""


@pytest.mark.parametrize("model_failure", [False, True])
def test_native_temporal_stream_publishes_before_model_completion(model_failure):
    async def scenario():
        workspace_id, run_id, profile_id = uuid4(), uuid4(), uuid4()
        request = ExecutionRequest(
            execution_id=run_id,
            workspace_id=workspace_id,
            resource_id=run_id,
            kind=ExecutionKind.AGENT,
        )
        definition = AgentExecutionDefinition(
            profile=AgentProfileSnapshot(
                profile_id, workspace_id, 1, "Expert", "expert", "Answer", "offline"
            ),
            context=AgentExecutionContext(workspace_id, None, run_id),
            model_reference=AgentModelReference(
                workspace_id=workspace_id,
                run_id=run_id,
                agent_profile_id=profile_id,
                credential_id=uuid4(),
                model="offline",
            ),
            provider="openai",
            input_text="Test streaming",
            max_turns=4,
            stream=True,
        )
        saw_delta, allow_finish = asyncio.Event(), asyncio.Event()
        recorded, completed, failures = [], [], []

        class StreamingModel(Model):
            async def get_response(self, **kwargs):
                raise AssertionError("Must use native streaming Activity")

            async def stream_response(self, **kwargs):
                yield ResponseTextDeltaEvent(
                    type="response.output_text.delta",
                    delta="live",
                    content_index=0,
                    output_index=0,
                    sequence_number=0,
                    item_id="message",
                    logprobs=[],
                )
                await allow_finish.wait()
                if model_failure:
                    raise ApplicationError(
                        "Provider unavailable",
                        {"status_code": 503},
                        type="provider_unavailable",
                        non_retryable=True,
                    )
                response = Response.model_validate(
                    {
                        "id": "response",
                        "created_at": 0,
                        "model": "offline",
                        "object": "response",
                        "status": "completed",
                        "parallel_tool_calls": False,
                        "tool_choice": "auto",
                        "tools": [],
                        "output": [
                            {
                                "id": "message",
                                "type": "message",
                                "role": "assistant",
                                "status": "completed",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "live answer",
                                        "annotations": [],
                                    }
                                ],
                            }
                        ],
                    }
                )
                yield ResponseCompletedEvent(
                    type="response.completed", sequence_number=1, response=response
                )

        class Provider(ModelProvider):
            def get_model(self, name):
                assert AgentModelReference.decode(name).credential_id == (
                    definition.model_reference.credential_id
                )
                return StreamingModel()

        class Events(AgentStreamActivities):
            def _persist(self, request, offset, event):
                recorded.append((offset, event))
                if event.delta == "live":
                    loop.call_soon_threadsafe(saw_delta.set)

        @activity.defn(name="opsmesh.agent.prepare")
        async def prepare(request: ExecutionRequest) -> AgentExecutionDefinition:
            return definition

        @activity.defn(name="opsmesh.agent.complete")
        async def complete(outcome: AgentExecutionOutcome) -> None:
            completed.append(outcome)

        @activity.defn(name="opsmesh.agent.lifecycle")
        async def lifecycle(notification: AgentLifecycleNotification) -> None:
            pass

        @activity.defn(name="opsmesh.agent.authorize")
        async def authorize(operation: AgentToolOperation) -> None:
            pass

        @activity.defn(name="opsmesh.agent.fail")
        async def fail(request: AgentExecutionFailure) -> None:
            failures.append(request)

        loop = asyncio.get_running_loop()
        events = Events(lambda: None)
        env = await WorkflowEnvironment.start_local(data_converter=sdk_data_converter)
        try:
            plugin = OpenAIAgentsPlugin(
                model_provider=Provider(),
                model_params=ModelActivityParameters(
                    start_to_close_timeout=timedelta(seconds=60),
                    streaming_topic=MODEL_EVENT_TOPIC,
                    heartbeat_timeout=timedelta(seconds=10),
                ),
            )
            async with Worker(
                env.client,
                task_queue="native-agent-test",
                workflows=[AgentWorkflow],
                activities=[
                    release_execution_boundary,
                    prepare,
                    complete,
                    lifecycle,
                    authorize,
                    fail,
                    events.relay,
                ],
                plugins=[plugin],
            ):
                handle = await env.client.start_workflow(
                    AgentWorkflow.run,
                    request,
                    id=request.workflow_id,
                    task_queue="native-agent-test",
                )
                await asyncio.wait_for(saw_delta.wait(), 30)
                assert completed == []
                allow_finish.set()
                if model_failure:
                    from temporalio.client import WorkflowFailureError

                    with pytest.raises(WorkflowFailureError) as failure:
                        await asyncio.wait_for(handle.result(), 30)
                    assert failure.value.cause.type == "provider_unavailable"
                    assert failures[0].metadata["status_code"] == 503
                    assert not completed
                else:
                    await asyncio.wait_for(handle.result(), 30)
                    assert completed[0].result.final_output == "live answer"
                    assert any(event.is_terminal for _, event in recorded)
                    assert not failures
                assert len({offset for offset, _ in recorded}) == len(recorded)
        finally:
            await env.shutdown()

    asyncio.run(scenario())


def test_native_temporal_approval_survives_worker_restart_and_preserves_tool_identity():
    from agents import ModelResponse
    from agents.usage import Usage
    from openai.types.responses import (
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputText,
    )
    from temporalio.openai_agents.testing import TestModel, TestModelProvider

    from opsmesh.agents.execution.contracts import (
        AgentToolApprovalDecision,
        AgentToolSpec,
        ToolExecutionResult,
    )
    from opsmesh.agents.execution.providers.openai.workflow import AgentApprovalResume
    from opsmesh.runtime.temporal.contracts import WorkflowSignal

    async def scenario():
        request, definition = _execution_definition()
        definition = definition.model_copy(
            update={
                "context": AgentExecutionContext(
                    request.workspace_id,
                    None,
                    request.resource_id,
                    allowed_tools=("inspect_logs",),
                    tool_definitions=(
                        AgentToolSpec(
                            name="inspect_logs",
                            source="product",
                            description="Inspect",
                            input_schema={"type": "object", "properties": {}},
                        ),
                    ),
                ),
            }
        )
        responses = [
            ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        type="function_call",
                        name="inspect_logs",
                        call_id="original-call",
                        arguments="{}",
                    )
                ],
                usage=Usage(),
                response_id="tool",
            ),
            ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="answer",
                        type="message",
                        role="assistant",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                type="output_text",
                                text="analyzed",
                                annotations=[],
                            )
                        ],
                    )
                ],
                usage=Usage(),
                response_id="answer",
            ),
        ]
        paused, calls, outcomes, resumes = asyncio.Event(), [], [], []
        approval_id = uuid4()

        @activity.defn(name="opsmesh.agent.prepare")
        async def prepare(request: ExecutionRequest) -> AgentExecutionDefinition:
            return definition

        @activity.defn(name="opsmesh.agent.complete")
        async def complete(outcome: AgentExecutionOutcome) -> None:
            outcomes.append(outcome)
            if outcome.result.interruptions:
                paused.set()

        @activity.defn(name="opsmesh.agent.lifecycle")
        async def lifecycle(notification: AgentLifecycleNotification) -> None:
            pass

        @activity.defn(name="opsmesh.agent.authorize")
        async def authorize(operation: AgentToolOperation) -> None:
            pass

        @activity.defn(name="opsmesh.agent.review_tool")
        async def review(operation: AgentToolOperation) -> dict[str, object]:
            return {"decision": "require_approval", "risk_level": "high"}

        @activity.defn(name="opsmesh.agent.execute_tool")
        async def execute(operation: AgentToolOperation) -> ToolExecutionResult:
            assert operation.approval_granted
            calls.append(operation.tool_call_id)
            return ToolExecutionResult(status="completed", output={"evidence": "original"})

        @activity.defn(name="opsmesh.agent.resume")
        async def resume(approval: AgentApprovalResume) -> AgentExecutionDefinition:
            assert approval.approval_id == approval_id
            resumes.append(approval)
            return definition.model_copy(
                update={
                    "resume_state": approval.state,
                    "approval_decisions": (
                        AgentToolApprovalDecision(
                            tool_call_id="original-call",
                            tool_name="inspect_logs",
                            status="approved",
                        ),
                    ),
                }
            )

        @activity.defn(name="opsmesh.agent.fail")
        async def fail(failure: AgentExecutionFailure) -> None:
            return None

        environment = await WorkflowEnvironment.start_local(data_converter=sdk_data_converter)
        plugin = OpenAIAgentsPlugin(
            model_provider=TestModelProvider(TestModel.returning_responses(responses)),
            model_params=ModelActivityParameters(start_to_close_timeout=timedelta(seconds=30)),
        )

        def worker():
            return Worker(
                environment.client,
                task_queue="agent-approval-test",
                workflows=[AgentWorkflow],
                activities=[
                    release_execution_boundary,
                    prepare,
                    complete,
                    lifecycle,
                    authorize,
                    review,
                    execute,
                    resume,
                    fail,
                ],
                plugins=[plugin],
            )

        try:
            async with worker():
                handle = await environment.client.start_workflow(
                    AgentWorkflow.run,
                    request,
                    id=request.workflow_id,
                    task_queue="agent-approval-test",
                )
                await asyncio.wait_for(paused.wait(), 30)
                assert calls == []
            await handle.signal(AgentWorkflow.approval, WorkflowSignal(resource_id=approval_id))
            await handle.signal(AgentWorkflow.approval, WorkflowSignal(resource_id=approval_id))
            async with worker():
                await asyncio.wait_for(handle.result(), 30)
            assert calls == ["original-call"]
            assert len(resumes) == 1
            assert outcomes[-1].result.final_output == "analyzed"
        finally:
            await environment.shutdown()

    asyncio.run(scenario())


def test_native_temporal_cancel_stops_model_activity_and_records_product_cancellation():
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import CancelledError
    from temporalio.openai_agents.testing import TestModelProvider

    async def scenario():
        request, definition = _execution_definition()
        started, stopped, cancelled = asyncio.Event(), asyncio.Event(), []

        class BlockingModel(Model):
            async def get_response(self, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            async def stream_response(self, **kwargs):
                raise AssertionError("Nonstreaming model")
                yield

        @activity.defn(name="opsmesh.agent.prepare")
        async def prepare(request: ExecutionRequest) -> AgentExecutionDefinition:
            return definition

        @activity.defn(name="opsmesh.agent.lifecycle")
        async def lifecycle(notification: AgentLifecycleNotification) -> None:
            pass

        @activity.defn(name="opsmesh.agent.authorize")
        async def authorize(operation: AgentToolOperation) -> None:
            pass

        @activity.defn(name="opsmesh.agent.cancel")
        async def cancel(request: ExecutionRequest) -> None:
            cancelled.append(request.resource_id)

        environment = await WorkflowEnvironment.start_local(data_converter=sdk_data_converter)
        plugin = OpenAIAgentsPlugin(
            model_provider=TestModelProvider(BlockingModel()),
            model_params=ModelActivityParameters(
                start_to_close_timeout=timedelta(seconds=60),
                heartbeat_timeout=timedelta(seconds=10),
            ),
        )
        try:
            async with Worker(
                environment.client,
                task_queue="agent-cancel-test",
                workflows=[AgentWorkflow],
                activities=[release_execution_boundary, prepare, lifecycle, authorize, cancel],
                plugins=[plugin],
            ):
                handle = await environment.client.start_workflow(
                    AgentWorkflow.run,
                    request,
                    id=request.workflow_id,
                    task_queue="agent-cancel-test",
                )
                await asyncio.wait_for(started.wait(), 30)
                await handle.cancel()
                with pytest.raises(WorkflowFailureError) as failure:
                    await asyncio.wait_for(handle.result(), 30)
                assert isinstance(failure.value.cause, CancelledError)
                await asyncio.wait_for(stopped.wait(), 15)
                assert cancelled == [request.resource_id]
        finally:
            await environment.shutdown()

    asyncio.run(scenario())


def test_permanent_prepare_rejection_is_terminal_and_never_retries():
    from temporalio.client import WorkflowFailureError

    async def scenario():
        request, _ = _execution_definition()
        attempts, failures = [], []

        @activity.defn(name="opsmesh.agent.prepare")
        async def prepare(request: ExecutionRequest) -> AgentExecutionDefinition:
            attempts.append(request.resource_id)
            raise ValueError("Frozen execution profile was revoked")

        @activity.defn(name="opsmesh.agent.fail")
        async def fail(failure: AgentExecutionFailure) -> None:
            failures.append(failure)

        environment = await WorkflowEnvironment.start_local(data_converter=sdk_data_converter)
        try:
            async with Worker(
                environment.client,
                task_queue="agent-rejection-test",
                workflows=[AgentWorkflow],
                activities=[release_execution_boundary, prepare, fail],
            ):
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(
                        environment.client.execute_workflow(
                            AgentWorkflow.run,
                            request,
                            id=request.workflow_id,
                            task_queue="agent-rejection-test",
                        ),
                        timeout=30,
                    )
                assert attempts == [request.resource_id]
                assert len(failures) == 1 and failures[0].definition is None
        finally:
            await environment.shutdown()

    asyncio.run(scenario())


def _execution_definition():
    workspace_id, run_id, profile_id = uuid4(), uuid4(), uuid4()
    request = ExecutionRequest(
        execution_id=run_id,
        workspace_id=workspace_id,
        resource_id=run_id,
        kind=ExecutionKind.AGENT,
    )
    return request, AgentExecutionDefinition(
        profile=AgentProfileSnapshot(
            profile_id, workspace_id, 1, "Expert", "expert", "Answer", "offline"
        ),
        context=AgentExecutionContext(workspace_id, None, run_id),
        model_reference=AgentModelReference(
            workspace_id=workspace_id,
            run_id=run_id,
            agent_profile_id=profile_id,
            credential_id=uuid4(),
            model="offline",
        ),
        provider="openai",
        input_text="Test",
        max_turns=4,
    )
