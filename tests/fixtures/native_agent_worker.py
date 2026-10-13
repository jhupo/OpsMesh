"""Real Temporal Worker and PG Session boundaries with an offline native Model."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

from agents import ModelProvider, ModelResponse
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import activity
from temporalio.openai_agents import OpenAIAgentsPlugin
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from opsmesh.agents.execution.providers.openai.activities import AgentExecutionActivities
from opsmesh.agents.execution.providers.openai.activity_policy import model_activity_parameters
from opsmesh.agents.execution.providers.openai.contracts import (
    AgentExecutionDefinition,
    AgentModelReference,
)
from opsmesh.agents.execution.providers.openai.sandbox_activities import AgentSandboxActivities
from opsmesh.agents.execution.providers.openai.sandbox_interceptor import (
    SandboxAdmissionInterceptor,
)
from opsmesh.agents.execution.providers.openai.stream_events import AgentStreamActivities
from opsmesh.agents.execution.providers.openai.workflow import AgentApprovalResume, AgentWorkflow
from opsmesh.agents.sessions.activities import SessionActivities
from opsmesh.orchestration.conversations.activities import ConversationActivities
from opsmesh.orchestration.conversations.workflow import ConversationWorkflow
from opsmesh.runtime.temporal.client import TemporalExecutionClient
from opsmesh.runtime.temporal.contracts import ExecutionRequest
from opsmesh.runtime.temporal.converter import sdk_data_converter
from opsmesh.runtime.temporal.outbox import TemporalOutboxPublisher
from opsmesh.shared.config import Settings


def answer(value):
    return ModelResponse(
        output=[
            ResponseOutputMessage(
                id="offline-answer",
                type="message",
                role="assistant",
                status="completed",
                content=[ResponseOutputText(type="output_text", text=value, annotations=[])],
            )
        ],
        usage=Usage(requests=1),
        response_id="offline-response",
    )


def stream_delta(value):
    return ResponseTextDeltaEvent(
        type="response.output_text.delta",
        delta=value,
        content_index=0,
        output_index=0,
        sequence_number=0,
        item_id="offline-answer",
        logprobs=[],
    )


def stream_completed(value):
    return ResponseCompletedEvent(
        type="response.completed",
        sequence_number=1,
        response=Response.model_validate(
            {
                "id": "offline-response",
                "created_at": 0,
                "model": "gpt-4.1",
                "object": "response",
                "status": "completed",
                "parallel_tool_calls": False,
                "tool_choice": "auto",
                "tools": [],
                "output": answer(value).output,
            }
        ),
    )


def stream_item(value):
    return ResponseOutputItemDoneEvent(
        type="response.output_item.done",
        sequence_number=1,
        output_index=0,
        item=answer(value).output[0],
    )


class OfflineModelProvider(ModelProvider):
    def __init__(self, factory):
        self.factory = factory

    def get_model(self, name):
        assert activity.in_activity(), "SDK inference must execute inside the official Activity"
        return self.factory(AgentModelReference.decode(name))


@dataclass
class NativeWorker:
    state: object
    environment: WorkflowEnvironment
    products: AgentExecutionActivities
    streams: AgentStreamActivities
    sessions: SessionActivities
    database: object
    settings: Settings
    plugin: OpenAIAgentsPlugin
    executor: ThreadPoolExecutor
    task_queue: str
    stream: bool
    sandbox_admission: object | None = None
    sandbox_provider_names: tuple[str, ...] = ()

    @activity.defn(name="opsmesh.agent.prepare")
    async def prepare(self, request: ExecutionRequest) -> AgentExecutionDefinition | None:
        definition = await self.products.prepare(request)
        return (
            definition.model_copy(update={"stream": self.stream})
            if isinstance(definition, AgentExecutionDefinition)
            else None
        )

    @activity.defn(name="opsmesh.agent.resume")
    async def resume(self, approval: AgentApprovalResume) -> AgentExecutionDefinition | None:
        definition = await self.products.resume(approval)
        return definition.model_copy(update={"stream": self.stream}) if definition else None

    def worker(self, *, additional_workflows=(), additional_activities=()):
        from opsmesh.orchestration.runs.activities import RunExecutionActivities

        conversations = ConversationActivities(self.state.sessions)
        return Worker(
            self.environment.client,
            task_queue=self.task_queue,
            workflows=[AgentWorkflow, ConversationWorkflow, *additional_workflows],
            activities=[
                RunExecutionActivities(self.state.sessions).release,
                *additional_activities,
                *(
                    [
                        self.sandbox_admission.prepare,
                        self.sandbox_admission.review,
                        self.sandbox_admission.admit,
                        self.sandbox_admission.finish,
                        self.sandbox_admission.checkpoint,
                    ]
                    if self.sandbox_admission
                    else []
                ),
                self.prepare,
                self.products.complete,
                self.products.lifecycle,
                self.resume,
                self.products.fail,
                self.products.cancel,
                self.products.authorize,
                self.products.review_tool,
                self.products.execute_tool,
                self.products.cancel_tools,
                self.streams.relay,
                self.sessions.operate,
                conversations.advance,
            ],
            plugins=[self.plugin],
            interceptors=[
                SandboxAdmissionInterceptor(self.sandbox_admission, self.sandbox_provider_names)
            ]
            if self.sandbox_admission
            else [],
            activity_executor=self.executor,
            max_concurrent_activities=16,
            max_heartbeat_throttle_interval=timedelta(seconds=1),
        )

    def publisher(self):
        return TemporalOutboxPublisher(
            self.database,
            TemporalExecutionClient(self.environment.client, task_queue=self.task_queue),
        )


@asynccontextmanager
async def native_agent_worker(state, provider, *, stream=False, sandbox_backends=None):
    bind = state.sessions.kw["bind"]
    with bind.connect() as connection:
        search_path = connection.scalar(text("SHOW search_path"))
    engine = create_async_engine(bind.url, connect_args={"options": f"-csearch_path={search_path}"})
    sessions = SessionActivities(engine, state.sessions)
    environment = await WorkflowEnvironment.start_local(data_converter=sdk_data_converter)
    settings = Settings(environment="test", model_request_timeout_seconds=60)
    try:
        with ThreadPoolExecutor(max_workers=16) as executor:
            yield NativeWorker(
                state,
                environment,
                state.activities,
                AgentStreamActivities(state.sessions),
                sessions,
                async_sessionmaker(engine, expire_on_commit=False),
                settings,
                OpenAIAgentsPlugin(
                    model_provider=provider,
                    model_params=model_activity_parameters(settings),
                    sandbox_clients=sandbox_backends.temporal_providers()
                    if sandbox_backends
                    else [],
                ),
                executor,
                "native-product-validation",
                stream,
                AgentSandboxActivities(state.sessions, settings, sandbox_backends)
                if sandbox_backends
                else None,
                tuple(provider.name for provider in sandbox_backends.temporal_providers())
                if sandbox_backends
                else (),
            )
    finally:
        await environment.shutdown()
        await sessions.close()
        await engine.dispose()
