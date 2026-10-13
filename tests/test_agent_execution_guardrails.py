from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents import RunContextWrapper
from agents.exceptions import InputGuardrailTripwireTriggered

import opsmesh.agents.execution.providers.openai.executor as openai_runtime
from opsmesh.agents.execution.contracts import (
    AgentExecutionContext,
    AgentGuardrailConfig,
    AgentGuardrailSpec,
    AgentOutputSchemaSpec,
    AgentRunRequest,
)
from opsmesh.agents.execution.errors import (
    AgentGuardrailBlockedError,
    AgentOutputValidationError,
    normalize_agent_error,
)
from opsmesh.agents.execution.providers.openai.executor import OpenAIAgentExecutor
from opsmesh.agents.execution.providers.openai.guardrails import SDKOutputSchemaAdapter
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.profiles.service import AgentProfileService
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.orchestration.requests.builder import RunRequestBuilder
from opsmesh.orchestration.runs.authorization.snapshot import RunAuthorizationSnapshotService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.result_payloads import run_output_payload
from opsmesh.orchestration.tasks.models import Task
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.config import Settings
from tests.fixtures.agent_product import (  # noqa: F401
    _seed_workspace,
    _session,
)


def test_openai_sdk_applies_structured_output_and_guardrails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        guardrails=AgentGuardrailConfig(
            input=(
                AgentGuardrailSpec(
                    name="input-safety",
                    kind="blocked_terms",
                    config={"terms": ["blocked"]},
                ),
            ),
            output=(
                AgentGuardrailSpec(
                    name="output-review",
                    kind="blocked_terms",
                    config={"terms": ["done"]},
                    blocking=False,
                ),
            ),
        )
    )

    async def fake_run(*args: object, **kwargs: object) -> object:
        agent = kwargs.get("starting_agent") or args[0]
        value = {"answer": "done"}
        context = RunContextWrapper(context=kwargs["context"])
        for guardrail in agent.input_guardrails:
            guardrail_result = await guardrail.run(agent, args[1], context)
            assert guardrail_result.output.tripwire_triggered is False
            json.dumps(guardrail_result.output.output_info)
        for guardrail in agent.output_guardrails:
            guardrail_result = await guardrail.run(context, agent, value)
            assert guardrail_result.output.tripwire_triggered is False
        return SimpleNamespace(
            final_output=value,
            context_wrapper=context,
            last_agent=agent,
            interruptions=[],
            new_items=[],
            events=[],
            usage=None,
        )

    monkeypatch.setattr(openai_runtime.Runner, "run", fake_run)
    result = asyncio.run(OpenAIAgentExecutor().run(request))
    agent = OpenAIAgentExecutor()._build_agent(request)

    assert isinstance(agent.output_type, SDKOutputSchemaAdapter)
    assert agent.output_type.name() == "answer"
    schema = agent.output_type.json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert json.loads(result.final_output) == {"answer": "done"}
    assert result.structured_output is not None
    assert result.structured_output.schema_version == "v1"
    assert result.structured_output.validated is True
    assert [item.status for item in result.guardrail_results] == ["passed", "flagged"]
    assert any(event.event_type == "agent.guardrail.flagged" for event in result.events)
    assert run_output_payload(result)["guardrail_results"][1]["status"] == "flagged"


def test_openai_sdk_guardrail_block_is_non_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        input_text="This contains blocked content.",
        guardrails=AgentGuardrailConfig(
            input=(
                AgentGuardrailSpec(
                    name="input-safety",
                    kind="blocked_terms",
                    config={"terms": ["blocked"]},
                ),
            ),
        ),
    )
    calls = 0

    async def fake_run(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        agent = kwargs.get("starting_agent") or args[0]
        context = RunContextWrapper(context=kwargs["context"])
        result = await agent.input_guardrails[0].run(agent, args[1], context)
        raise InputGuardrailTripwireTriggered(result)

    monkeypatch.setattr(openai_runtime.Runner, "run", fake_run)

    with pytest.raises(AgentGuardrailBlockedError) as raised:
        asyncio.run(OpenAIAgentExecutor().run(request))

    normalized = normalize_agent_error(raised.value)
    assert calls == 1
    assert normalized.code == "agent_guardrail_blocked"
    assert normalized.retryable is False
    assert raised.value.metadata["evidence"] == {
        "evaluated_characters": len(request.input_text),
        "match_count": 1,
    }
    assert "blocked content" not in json.dumps(raised.value.metadata)


def test_openai_structured_output_fails_closed_on_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.items import ModelResponse
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import ResponseOutputMessage, ResponseOutputText

    class InvalidOutputModel(Model):
        async def get_response(self, **kwargs):
            return ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="invalid",
                        type="message",
                        role="assistant",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                type="output_text", text='{"wrong":true}', annotations=[]
                            )
                        ],
                    )
                ],
                usage=Usage(requests=1),
                response_id="offline",
            )

        async def stream_response(self, *args, **kwargs):
            raise AssertionError("Streaming is disabled")
            yield

    from temporalio.openai_agents.testing import TestModelProvider

    from tests.fixtures.sdk import install_model_provider

    install_model_provider(monkeypatch, TestModelProvider(InvalidOutputModel()))

    with pytest.raises(AgentOutputValidationError) as raised:
        asyncio.run(OpenAIAgentExecutor().run(_request()))

    assert normalize_agent_error(raised.value).retryable is False
    assert {
        key: raised.value.metadata[key] for key in ("schema_name", "schema_version", "validator")
    } == {
        "schema_name": "answer",
        "schema_version": "v1",
        "validator": "required",
    }
    assert len(raised.value.metadata["model_usages"]) == 1


def test_openai_waiting_approval_defers_final_output_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class State:
        def to_json(self, **_: object) -> dict[str, object]:
            return {"$schemaVersion": "1.10"}

    async def fake_run(*args: object, **kwargs: object) -> object:
        agent = kwargs.get("starting_agent") or args[0]
        interruption = SimpleNamespace(
            call_id="approval-call",
            name="approval-tool",
            arguments="{}",
            agent=agent,
        )
        return SimpleNamespace(
            final_output=None,
            context_wrapper=RunContextWrapper(kwargs["context"]),
            last_agent=agent,
            new_items=[],
            events=[],
            usage=None,
            interruptions=[interruption],
            to_state=lambda: State(),
        )

    monkeypatch.setattr(openai_runtime.Runner, "run", fake_run)
    result = asyncio.run(OpenAIAgentExecutor().run(_request()))

    assert result.structured_output is None
    assert result.resume_state is not None
    assert result.interruptions[0].tool_call_id == "approval-call"


def test_runtime_controls_are_frozen_and_hydrated_for_worker() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    profile = AgentProfile(
        workspace_id=workspace.id,
        name="Structured Agent",
        role="worker",
        instructions="Return structured output.",
        model="gpt-4.1",
        model_settings={"output_schema": _output_schema_config()},
        runtime_policy={"guardrails": _guardrail_config()},
        id=uuid4(),
    )
    session.add(profile)
    session.flush()
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Structured task",
    )
    session.add(task)
    session.flush()
    builder = RunRequestBuilder(session, Settings(environment="test"))
    snapshot = RunAuthorizationSnapshotService(
        session,
        builder,
    ).build_authorization_snapshot(task, None, profile)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=profile.id,
        input={"authorization_snapshot": snapshot},
    )
    session.add(run)
    profile.model_settings = {}
    profile.runtime_policy = {}
    session.commit()

    request = builder.build_agent_request(
        run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=run.id,
            requested_by_user_id=user.id,
        ),
    )

    assert snapshot["output_schema"] == _output_schema_config()
    assert snapshot["guardrails"] == _guardrail_config()
    assert request.output_schema is not None
    assert request.output_schema.name == "answer"
    assert request.guardrails is not None
    assert request.guardrails.input[0].name == "input-safety"


@pytest.mark.parametrize(
    ("kind", "config"),
    [
        ("blocked_terms", {"terms": [1]}),
        ("blocked_terms", {"terms": ["secret"], "case_sensitive": "false"}),
        ("max_characters", {"max_characters": True}),
        ("max_characters", {"max_characters": "10"}),
        ("unknown", {"schema": {"type": "string"}}),
    ],
)
def test_guardrail_execution_rejects_invalid_direct_contract(
    kind: str, config: dict[str, object]
) -> None:
    from opsmesh.agents.execution.guardrails import evaluate_guardrail

    with pytest.raises(ValueError):
        evaluate_guardrail(
            AgentGuardrailSpec(name="invalid", kind=kind, config=config),
            "safe input",
            stage="input",
        )


def test_agent_profile_rejects_invalid_guardrail_configuration() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)

    with pytest.raises(ValueError, match="unsupported kind"):
        AgentProfileService(session, Settings(environment="test")).create_agent(
            workspace.id,
            {
                "name": "Invalid Guardrail Agent",
                "role": "worker",
                "runtime_policy": {
                    "guardrails": {
                        "input": [
                            {
                                "name": "unsafe-custom-code",
                                "kind": "python_callback",
                                "config": {},
                            }
                        ]
                    }
                },
            },
        )


def _request(**changes: object) -> AgentRunRequest:
    workspace_id = uuid4()
    provider = str(changes.pop("provider", "openai"))
    model = str(changes.pop("model", "gpt-4.1-mini"))
    profile = AgentProfile(
        workspace_id=workspace_id,
        name="Structured Agent",
        role="worker",
        instructions="Return one structured answer.",
        model=model,
        model_settings={},
        id=uuid4(),
    )
    values = {
        "agent_profile": profile,
        "input_text": "Return an answer.",
        "context": AgentExecutionContext(
            workspace_id=workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        "provider": provider,
        "model": model,
        "api_key": "provider-test-key",
        "output_schema": AgentOutputSchemaSpec(
            name="answer",
            schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
            version="v1",
        ),
    }
    values.update(changes)
    return AgentRunRequest(model_provider_credential_id=uuid4(), **values)


def _output_schema_config() -> dict[str, object]:
    return {
        "name": "answer",
        "schema": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
        "strict": True,
        "version": "v1",
    }


def _guardrail_config() -> dict[str, object]:
    return {
        "input": [
            {
                "name": "input-safety",
                "kind": "blocked_terms",
                "config": {"terms": ["blocked"], "case_sensitive": False},
                "blocking": True,
            }
        ],
        "output": [],
    }
