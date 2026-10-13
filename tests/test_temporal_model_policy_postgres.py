"""Actual model Activity rejection is permanent and independent of MCP timeouts."""

import asyncio
import json
import os

import httpx
import pytest
from openai import AsyncOpenAI
from sqlalchemy import select
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.openai_agents import OpenAIAgentsPlugin
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from opsmesh.agents.execution.providers.openai import model_provider
from opsmesh.agents.execution.providers.openai.activity_policy import model_activity_parameters
from opsmesh.agents.execution.providers.openai.contracts import AgentExecutionDefinition
from opsmesh.agents.execution.providers.openai.model_provider import build_model_provider
from opsmesh.agents.execution.providers.openai.workflow import AgentWorkflow
from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.runtime.temporal.contracts import ExecutionRequest
from opsmesh.runtime.temporal.converter import sdk_data_converter
from opsmesh.shared.config import Settings

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL") or os.getenv("OPSMESH_TEST_TEMPORAL") != "1",
    reason="Real PostgreSQL and Temporal required",
)


@pytest.mark.parametrize("rejection", ["revoked_user", "invalid_credential"])
def test_permanent_model_rejection_attempts_once_with_separate_timeout(
    native_execution, rejection, monkeypatch
):
    state = native_execution
    attempts = []
    settings = Settings(environment="test", mcp_tool_timeout_seconds=1)
    provider = build_model_provider(state.sessions, settings)
    original_get_model = provider.get_model

    def get_model(name):
        attempts.append(activity.info().attempt)
        with state.sessions.begin() as session:
            if rejection == "revoked_user":
                session.get(User, state.user_id).status = "disabled"
            else:
                credential = session.get(
                    ModelProviderCredential,
                    AgentExecutionDefinition.model_validate_json(
                        definition_json
                    ).model_reference.credential_id,
                )
                credential.encrypted_api_key = "invalid-encrypted-credential"
        return original_get_model(name)

    monkeypatch.setattr(provider, "get_model", get_model)

    @activity.defn(name="opsmesh.agent.prepare")
    async def prepare(request: ExecutionRequest) -> AgentExecutionDefinition:
        nonlocal definition_json
        definition = await state.activities.prepare(request)
        definition_json = definition.model_dump_json()
        return definition.model_copy(update={"session": None})

    definition_json = ""

    async def scenario():
        from opsmesh.orchestration.runs.activities import RunExecutionActivities

        environment = await WorkflowEnvironment.start_local(data_converter=sdk_data_converter)
        try:
            async with Worker(
                environment.client,
                task_queue="permanent-model-rejection",
                workflows=[AgentWorkflow],
                activities=[
                    RunExecutionActivities(state.sessions).release,
                    prepare,
                    state.activities.fail,
                    state.activities.cancel,
                    state.activities.authorize,
                    state.activities.lifecycle,
                ],
                plugins=[
                    OpenAIAgentsPlugin(
                        model_provider=provider, model_params=model_activity_parameters(settings)
                    )
                ],
            ):
                request = state.request
                handle = await environment.client.start_workflow(
                    AgentWorkflow.run,
                    request,
                    id=request.workflow_id,
                    task_queue="permanent-model-rejection",
                )
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(handle.result(), timeout=45)
                history = await handle.fetch_history()
                scheduled = [
                    event.activity_task_scheduled_event_attributes
                    for event in history.events
                    if event.HasField("activity_task_scheduled_event_attributes")
                    and "model" in event.activity_task_scheduled_event_attributes.activity_type.name
                ]
                assert len(scheduled) == 1
                assert scheduled[0].start_to_close_timeout.seconds == 600
                assert scheduled[0].retry_policy.maximum_attempts == 3
        finally:
            await environment.shutdown()

    asyncio.run(scenario())
    assert attempts == [1]
    with state.sessions() as session:
        assert session.get(AgentRun, state.request.resource_id).status == "failed"


@pytest.mark.parametrize("status", [401, 503])
def test_model_failure_error_identity_survives_native_activity_and_pg_projection(
    native_execution, status, monkeypatch, caplog
):
    state = native_execution
    settings = Settings(environment="test")
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={"error": {"message": "private-provider-canary sk-body-canary"}},
            headers={"x-request-id": "req-provider-failure"},
        )

    def client(**kwargs):
        return AsyncOpenAI(
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)), **kwargs
        )

    monkeypatch.setattr(model_provider, "AsyncOpenAI", client)

    @activity.defn(name="opsmesh.agent.prepare")
    async def prepare(request: ExecutionRequest) -> AgentExecutionDefinition:
        return (await state.activities.prepare(request)).model_copy(update={"session": None})

    async def scenario():
        from opsmesh.orchestration.runs.activities import RunExecutionActivities

        async with (
            await WorkflowEnvironment.start_local(
                data_converter=sdk_data_converter,
                dev_server_log_level="error",
            ) as environment,
            Worker(
                environment.client,
                task_queue="model-failure-evidence",
                workflows=[AgentWorkflow],
                activities=[
                    RunExecutionActivities(state.sessions).release,
                    prepare,
                    state.activities.fail,
                    state.activities.cancel,
                    state.activities.authorize,
                    state.activities.lifecycle,
                ],
                plugins=[
                    OpenAIAgentsPlugin(
                        model_provider=build_model_provider(state.sessions, settings),
                        model_params=model_activity_parameters(settings),
                    )
                ],
            ),
        ):
            handle = await environment.client.start_workflow(
                AgentWorkflow.run,
                state.request,
                id=state.request.workflow_id,
                task_queue="model-failure-evidence",
            )
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(handle.result(), timeout=45)
            assert "private-provider-canary" not in str(await handle.fetch_history())

    asyncio.run(scenario())
    expected_attempts = 1 if status == 401 else 3
    records = [record for record in caplog.records if record.name == model_provider.__name__]
    assert len(calls) == len(records) == expected_attempts
    with state.sessions() as session:
        run = session.get(AgentRun, state.request.resource_id)
        event = session.scalar(
            select(RunEvent).where(
                RunEvent.workspace_id == state.request.workspace_id,
                RunEvent.agent_run_id == run.id,
                RunEvent.event_type == "run.failed",
            )
        )
        assert run.status == "failed"
        assert event.event_metadata["error_id"] == records[-1].error_id
        assert run.error["metadata"]["error_id"] == records[-1].error_id
        assert event.event_metadata["provider_request_id"] == "req-provider-failure"
        assert event.event_metadata["status_code"] == status
        assert "exception_frames" not in event.event_metadata
        assert "private-provider-canary" not in json.dumps(run.error)
