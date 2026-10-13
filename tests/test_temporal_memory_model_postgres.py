"""SDK memory model Activities reauthorize references and meter native responses."""

import asyncio
import os
from dataclasses import replace

import httpx
import pytest
from agents import ModelSettings
from agents.models.interface import ModelTracing
from sqlalchemy import select
from temporalio import activity
from temporalio.testing import ActivityEnvironment

from opsmesh.agents.execution.providers.openai.contracts import AgentModelReference
from opsmesh.agents.execution.providers.openai.model_provider import build_model_provider
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.agents.providers.probes import ModelProviderHealthTarget, OpenAICompatibleHealthProbe
from opsmesh.governance.costs.models import ModelUsageRecord
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.service import RunOrchestrationService
from opsmesh.orchestration.tasks.models import Task
from opsmesh.shared.config import Settings

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


@pytest.mark.parametrize("model_api", [None, "responses"])
def test_memory_model_uses_activity_credentials_and_meters_response_once(
    native_execution, monkeypatch, model_api
):
    import opsmesh.agents.execution.providers.openai.model_provider as module

    state = native_execution
    settings = Settings(environment="test")
    native_client = module.AsyncOpenAI
    calls = []

    def response(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "id": "memory-response",
                "created_at": 0,
                "object": "response",
                "model": "gpt-4.1",
                "status": "completed",
                "parallel_tool_calls": False,
                "tool_choice": "auto",
                "tools": [],
                "output": [
                    {
                        "id": "memory-summary",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "remembered", "annotations": []}
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    def client(**kwargs):
        return native_client(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(response))
        )

    monkeypatch.setattr(module, "AsyncOpenAI", client)
    with state.sessions() as session:
        profile = session.get(AgentProfile, state.profile_id)
        profile.memory_policy = {"sdk_memory": {"enabled": True, "generate": True}}
        profile.model_settings = {"model_api": model_api} if model_api is not None else {}
        task = Task(
            workspace_id=state.request.workspace_id,
            created_by_user_id=state.user_id,
            owner_agent_profile_id=profile.id,
            title="Authorized native memory generation",
            status="queued",
            execution_identity=ExecutionIdentityService(session).capture(
                state.request.workspace_id, state.user_id
            ),
        )
        session.add(task)
        session.flush()
        orchestration = RunOrchestrationService(session)
        run = orchestration.create_queued_run_for_task(task)
        orchestration._run_lifecycle().mark_run_started(run)
        reference = AgentModelReference(
            workspace_id=run.workspace_id,
            run_id=run.id,
            agent_profile_id=profile.id,
            credential_id=run.input["authorization_snapshot"]["model_provider"]["credential_id"],
            model="gpt-4.1",
            model_api=model_api,
            purpose="memory",
        )
        session.commit()
        if model_api is None:
            # A later credential edit cannot change the admitted default protocol.
            credential = session.get(ModelProviderCredential, reference.credential_id)
            credential.budget_metadata = {"model_api": "chat_completions"}
            session.commit()

    @activity.defn
    async def infer(reference: AgentModelReference) -> None:
        model = build_model_provider(state.sessions, settings).get_model(reference.encode())
        result = await model.get_response(
            system_instructions="Summarize memory",
            input="Authorized notes",
            model_settings=ModelSettings(),
            tools=[],
            output_schema=None,
            handoffs=[],
            tracing=ModelTracing.DISABLED,
        )
        assert result.output[0].content[0].text == "remembered"

    async def scenario():
        environment = ActivityEnvironment()
        environment.info = replace(environment.info, activity_id="memory-phase")
        await environment.run(infer, reference)
        environment.info = replace(environment.info, attempt=2)
        await environment.run(infer, reference)
        with state.sessions() as session:
            run = session.get(AgentRun, reference.run_id)
            snapshot = dict(run.input["authorization_snapshot"])
            snapshot["memory_policy"] = {"sdk_memory": {"enabled": False}}
            run.input = {**run.input, "authorization_snapshot": snapshot}
            session.commit()
        with pytest.raises(ResourceAccessDeniedError):
            await environment.run(infer, reference)
        checks = await OpenAICompatibleHealthProbe(client_factory=client).probe(
            ModelProviderHealthTarget(
                provider="openai",
                model="gpt-4.1",
                api_key="sk-test",
                base_url=None,
                model_api=model_api,
            ),
            probes=("inference",),
            timeout_seconds=15,
        )
        assert checks[0].status == "passed"
        assert checks[0].metadata["model_api"] == "responses"

    asyncio.run(scenario())
    assert calls == ["/v1/responses", "/v1/responses", "/v1/responses"]
    assert "sk-unit-test-provider" not in reference.encode()
    with state.sessions() as session:
        records = session.scalars(
            select(ModelUsageRecord).where(
                ModelUsageRecord.workspace_id == reference.workspace_id,
                ModelUsageRecord.agent_run_id == reference.run_id,
            )
        ).all()
        assert len(records) == 1 and records[0].total_tokens == 15
