"""Native Temporal/Agents trace acceptance with a real local OTLP receiver."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import grpc
import httpx
from agents.models.openai_provider import OpenAIProvider
from openai import AsyncOpenAI
from opentelemetry import propagate, trace
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc
from sqlalchemy import create_engine
from temporalio.common import RetryPolicy
from temporalio.contrib.opentelemetry import OpenTelemetryPlugin
from temporalio.openai_agents import ModelActivityParameters, OpenAIAgentsPlugin
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from opsmesh.runtime.temporal.client import TemporalExecutionClient, connect_temporal
from opsmesh.runtime.temporal.contracts import (
    DispatchOperation,
    DispatchRequest,
    ExecutionKind,
    ExecutionRequest,
)
from opsmesh.shared.config import Settings
from opsmesh.shared.telemetry.tracing import create_telemetry_runtime
from tests.fixtures.traced_agent_workflow import TracedAgentWorkflow, observe_count


class TraceReceiver(trace_service_pb2_grpc.TraceServiceServicer):
    def __init__(self) -> None:
        self.requests = []

    def Export(self, request, context):
        self.requests.append(request)
        return trace_service_pb2.ExportTraceServiceResponse()


def verify() -> None:
    receiver = TraceReceiver()
    receiver_executor = ThreadPoolExecutor(max_workers=2)
    server = grpc.server(receiver_executor)
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(receiver, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    settings = Settings(
        environment="test",
        otel_exporter_otlp_endpoint=f"http://127.0.0.1:{port}",
        otel_exporter_otlp_insecure=True,
        otel_logs_enabled=False,
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    runtime = create_telemetry_runtime(settings, engine=engine)
    calls = []

    async def scenario():
        async def respond(request):
            calls.append(json.loads(request.content))
            if len(calls) == 1:
                return httpx.Response(
                    503,
                    json={"error": {"message": "private-provider-error-canary sk-private-canary"}},
                )
            message = (
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_probe",
                            "type": "function",
                            "function": {"name": "observe_count", "arguments": "{}"},
                        }
                    ],
                }
                if len(calls) == 2
                else {"role": "assistant", "content": "private-answer-canary"}
            )
            return httpx.Response(
                200,
                json={
                    "id": f"response-{len(calls)}",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "offline",
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "tool_calls" if len(calls) == 2 else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )

        async with AsyncOpenAI(
            api_key="sk-test-canary",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as model_client:
            agent_plugin = OpenAIAgentsPlugin(
                model_provider=OpenAIProvider(openai_client=model_client, use_responses=False),
                use_otel_instrumentation=True,
                add_temporal_spans=False,
                model_params=ModelActivityParameters(
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(milliseconds=100), maximum_attempts=2
                    ),
                ),
            )
            environment = await WorkflowEnvironment.start_local(
                dev_server_log_level="error"
            )
            try:
                configured = settings.model_copy(
                    update={
                        "temporal_address": environment.client.service_client.config.target_host
                    }
                )
                client = await connect_temporal(configured)
                request = ExecutionRequest(
                    workspace_id=uuid4(), resource_id=uuid4(), kind=ExecutionKind.AUDIT_INTEGRITY
                )
                with trace.get_tracer(__name__).start_as_current_span(
                    "acceptance.api"
                ) as submitted:
                    carrier = {}
                    propagate.inject(carrier)
                    command = DispatchRequest(
                        dispatch_id=uuid4(),
                        workspace_id=request.workspace_id,
                        workflow_id=request.workflow_id,
                        operation=DispatchOperation.START,
                        execution=request,
                        traceparent=carrier["traceparent"],
                    )
                async with Worker(
                    client,
                    task_queue="traced-agent",
                    workflows=[TracedAgentWorkflow],
                    activities=[observe_count],
                    plugins=[agent_plugin],
                ):
                    await TemporalExecutionClient(
                        client,
                        task_queue="traced-agent",
                        workflows={ExecutionKind.AUDIT_INTEGRITY: "acceptance.traced_agent"},
                    ).dispatch(command)
                    handle = client.get_workflow_handle(request.workflow_id)
                    assert await asyncio.wait_for(handle.result(), 30) == "private-answer-canary"
                    history = await handle.fetch_history()
                assert runtime.provider.force_flush(5_000)
                before_replay = sum(
                    len(scope.spans)
                    for batch in receiver.requests
                    for resource in batch.resource_spans
                    for scope in resource.scope_spans
                )
                await Replayer(
                    workflows=[TracedAgentWorkflow],
                    plugins=[OpenTelemetryPlugin(add_temporal_spans=True), agent_plugin],
                    data_converter=client.data_converter,
                ).replay_workflow(history)
                assert runtime.provider.force_flush(5_000)
                spans = [
                    span
                    for batch in receiver.requests
                    for resource in batch.resource_spans
                    for scope in resource.scope_spans
                    for span in scope.spans
                ]
                assert len(spans) == before_replay, "Replay exported duplicate spans"
                assert len(calls) == 3, "Replay or nested retries called the model again"
                expected = submitted.get_span_context().trace_id.to_bytes(16, "big")
                assert all(span.trace_id == expected for span in spans)
                workflows = [
                    span for span in spans if span.name == "RunWorkflow:acceptance.traced_agent"
                ]
                assert (
                    len(workflows) == 1
                    and workflows[0].end_time_unix_nano > workflows[0].start_time_unix_nano
                )
                assert any(
                    attribute.key == "openinference.span.kind"
                    and attribute.value.string_value == "LLM"
                    for span in spans
                    for attribute in span.attributes
                )
                assert any(
                    attribute.key == "openinference.span.kind"
                    and attribute.value.string_value == "TOOL"
                    for span in spans
                    for attribute in span.attributes
                )
                evidence = str(receiver.requests)
                assert "private-" not in evidence and "sk-test" not in evidence, evidence
                print(
                    json.dumps(
                        {
                            "spans": len(spans),
                            "model_requests": len(calls),
                            "workflow_spans": len(workflows),
                            "replay_duplicates": 0,
                            "native_otlp_received": True,
                        }
                    )
                )
            finally:
                await environment.shutdown()

    try:
        asyncio.run(scenario())
    finally:
        runtime.shutdown()
        engine.dispose()
        server.stop(0).wait(5)
        receiver_executor.shutdown()
