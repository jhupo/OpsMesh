"""Model Activity failures remain diagnosable without retaining provider payloads."""

import asyncio
import json
import logging
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from agents import ModelSettings
from agents.models.interface import ModelTracing
from openai import AsyncOpenAI
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DefaultFailureConverter
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from opsmesh.agents.execution.providers.openai import model_provider
from opsmesh.agents.execution.providers.openai.contracts import AgentModelReference
from opsmesh.agents.execution.providers.openai.workflow import AgentExecutionFailure
from opsmesh.agents.providers.contracts import ResolvedModelProvider
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.converter import sdk_data_converter
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.shared.telemetry.logging import RequestContextFilter, json_log_formatter
from tests.fixtures.log_context import log_context


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("status", "provider_request_id", "retryable"),
    [
        (401, "req-provider-1", False),
        (429, "req-provider-1", True),
        (503, "req-provider-1", True),
        (None, None, True),
        (400, "https://provider.invalid/private?api_key=sk-header-secret", False),
        (400, "sk-header-secret", False),
    ],
)
def test_native_model_failure_records_safe_activity_evidence(
    stream, status, provider_request_id, retryable, monkeypatch, caplog
):
    reference = AgentModelReference(
        workspace_id=uuid4(),
        run_id=uuid4(),
        agent_profile_id=uuid4(),
        credential_id=uuid4(),
        model="gpt-4.1",
    )
    requests = []
    clients = []

    def respond(request):
        requests.append(request)
        if status is None:
            raise httpx.ConnectError(
                "private-network-error https://provider.invalid/private sk-transport-secret",
                request=request,
            )
        return httpx.Response(
            status,
            json={
                "error": {
                    "message": "private-provider-body private-prompt sk-body-secret",
                    "type": "provider_error",
                }
            },
            headers={"x-request-id": provider_request_id},
        )

    def client(**kwargs):
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        clients.append(http_client)
        return AsyncOpenAI(http_client=http_client, **kwargs)

    def resolve(self, actual_reference):
        assert actual_reference == reference
        return ResolvedModelProvider(
            "openai",
            "gpt-4.1",
            "https://provider.invalid/private",
            "sk-model-secret",
            None,
            reference.credential_id,
        )

    monkeypatch.setattr(model_provider.AuthorizedModelResolver, "resolve", resolve)
    monkeypatch.setattr(model_provider, "AsyncOpenAI", client)
    caplog.set_level(logging.ERROR, logger=model_provider.__name__)
    environment = ActivityEnvironment()
    environment.info = replace(
        environment.info,
        activity_id="model-activity-1",
        activity_type="invoke_model",
        workflow_id="agent-workflow-1",
        workflow_run_id="temporal-run-1",
        attempt=2,
    )
    model = model_provider.ActivityAuthorizedModel(
        reference,
        lambda: None,
        SecretEncryptionService(secret="test-encryption-secret", key_id="test"),
        timeout_seconds=10,
    )

    async def invoke():
        arguments = {
            "system_instructions": None,
            "input": "private-prompt",
            "model_settings": ModelSettings(),
            "tools": [],
            "output_schema": None,
            "handoffs": [],
            "tracing": ModelTracing.DISABLED,
        }
        with log_context(request_id="api-request-1"):
            if stream:
                async for _ in model.stream_response(**arguments):
                    pass
            else:
                await model.get_response(**arguments)

    async def scenario():
        with pytest.raises(ApplicationError) as captured:
            await environment.run(invoke)
        return captured.value

    failure = asyncio.run(scenario())
    assert len(requests) == 1  # The native client must not add a second retry layer.
    assert all(item.is_closed for item in clients)
    assert failure.type == "model_provider_failed"
    assert failure.non_retryable is not retryable
    assert failure.__cause__ is None
    assert failure.__suppress_context__ is True
    assert len(failure.details) == 1
    diagnostic = failure.details[0]
    assert diagnostic["status_code"] == status
    assert diagnostic["model"] == "gpt-4.1"
    expected_request_id = "req-provider-1" if provider_request_id == "req-provider-1" else None
    assert diagnostic["provider_request_id"] == expected_request_id
    assert len(diagnostic["error_id"]) == 32
    assert "exception_frames" not in diagnostic  # Operator evidence stays out of Run metadata.

    serialized_failure = Failure()
    converter = DefaultFailureConverter()
    converter.to_failure(failure, sdk_data_converter.payload_converter, serialized_failure)
    assert not serialized_failure.HasField("cause")
    restored_failure = converter.from_failure(
        serialized_failure, sdk_data_converter.payload_converter
    )
    projection = AgentExecutionFailure.from_exception(
        ExecutionRequest(
            workspace_id=reference.workspace_id,
            resource_id=reference.run_id,
            kind=ExecutionKind.AGENT,
        ),
        restored_failure,
    )
    assert projection.metadata["error_id"] == diagnostic["error_id"]
    assert projection.metadata["provider_request_id"] == expected_request_id
    assert "exception_frames" not in projection.metadata

    records = [record for record in caplog.records if record.name == model_provider.__name__]
    assert len(records) == 1
    record = records[0]
    assert record.error_id == diagnostic["error_id"]
    assert record.workspace_id == str(reference.workspace_id)
    assert record.run_id == str(reference.run_id)
    assert record.agent_profile_id == str(reference.agent_profile_id)
    assert record.workflow_id == "agent-workflow-1"
    assert record.workflow_run_id == "temporal-run-1"
    assert record.activity_id == "model-activity-1"
    assert record.activity_type == "invoke_model"
    assert record.attempt == 2
    assert record.retryable is retryable
    assert record.provider_request_id == expected_request_id
    assert record.exc_info is None
    assert record.exception_frames
    assert len(record.exception_frames) <= 16
    assert any(frame["module"].startswith("openai.") for frame in record.exception_frames)
    assert all(set(frame) == {"module", "function", "line"} for frame in record.exception_frames)

    with log_context(request_id="api-request-1"):
        RequestContextFilter().filter(record)
    rendered = json_log_formatter().format(record)
    assert json.loads(rendered)["request_id"] == "api-request-1"
    evidence = (
        rendered
        + json.dumps(diagnostic)
        + str(failure)
        + str(serialized_failure)
        + projection.model_dump_json()
    )
    for sensitive in (
        "private-provider-body",
        "private-prompt",
        "private-network-error",
        "provider.invalid",
        "sk-model-secret",
        "sk-body-secret",
        "sk-header-secret",
        "sk-transport-secret",
    ):
        assert sensitive not in evidence
    assert len(rendered.encode()) < 16_384
