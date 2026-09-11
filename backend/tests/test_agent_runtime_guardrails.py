from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents import RunContextWrapper
from agents.exceptions import InputGuardrailTripwireTriggered
from sqlalchemy import select

import backend.app.agent_runtime.providers.openai_agents as openai_runtime
from backend.app.agent_runtime.claude.runner import ClaudeAgentSDKRunner
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRuntimeContext,
    AgentRuntimeGuardrail,
    AgentRuntimeGuardrailResult,
    AgentRuntimeGuardrails,
    AgentRuntimeOutputSchema,
)
from backend.app.agent_runtime.errors import (
    AgentRuntimeGuardrailBlockedError,
    AgentRuntimeOutputValidationError,
    normalize_agent_error,
)
from backend.app.agent_runtime.providers.openai_agents import OpenAIAgentsRunner
from backend.app.agent_runtime.openai.guardrails import OpenAIRuntimeOutputSchema
from backend.app.agents.models import AgentProfile
from backend.app.agents.profile_commands import AgentProfileCommandService
from backend.app.core.config import Settings
from backend.app.orchestration.run_authorization_snapshot import (
    RunAuthorizationSnapshotService,
)
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.orchestration.run_request.builder import RunRequestBuilder
from backend.app.orchestration.run_result_payloads import run_output_payload
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task
from backend.app.workers.jobs import JobPayload, JobType
from backend.tests.test_worker_run_execution import _seed_workspace, _session


def test_openai_sdk_applies_structured_output_and_guardrails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        guardrails=AgentRuntimeGuardrails(
            input=(
                AgentRuntimeGuardrail(
                    name="input-safety",
                    kind="blocked_terms",
                    config={"terms": ["blocked"]},
                ),
            ),
            output=(
                AgentRuntimeGuardrail(
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
            new_items=[],
            events=[],
            usage=None,
        )

    monkeypatch.setattr(openai_runtime.Runner, "run", fake_run)
    result = asyncio.run(OpenAIAgentsRunner().run(request))
    agent = OpenAIAgentsRunner()._build_agent(request)

    assert isinstance(agent.output_type, OpenAIRuntimeOutputSchema)
    assert agent.output_type.name() == "answer"
    assert agent.output_type.json_schema() == request.output_schema.schema
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
        guardrails=AgentRuntimeGuardrails(
            input=(
                AgentRuntimeGuardrail(
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

    with pytest.raises(AgentRuntimeGuardrailBlockedError) as raised:
        asyncio.run(OpenAIAgentsRunner().run(request))

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
    async def fake_run(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(
            final_output={"wrong": True},
            new_items=[],
            events=[],
            usage=None,
        )

    monkeypatch.setattr(openai_runtime.Runner, "run", fake_run)

    with pytest.raises(AgentRuntimeOutputValidationError) as raised:
        asyncio.run(OpenAIAgentsRunner().run(_request()))

    assert normalize_agent_error(raised.value).retryable is False
    assert raised.value.metadata == {
        "schema_name": "answer",
        "schema_version": "v1",
        "validator": "required",
    }


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
            new_items=[],
            events=[],
            usage=None,
            interruptions=[interruption],
            to_state=lambda: State(),
        )

    monkeypatch.setattr(openai_runtime.Runner, "run", fake_run)
    result = asyncio.run(OpenAIAgentsRunner().run(_request()))

    assert result.structured_output is None
    assert result.resume_state is not None
    assert result.interruptions[0].tool_call_id == "approval-call"


def test_claude_guardrail_blocks_before_provider_query() -> None:
    request = _request(
        provider="anthropic",
        model="claude-sonnet-4-5",
        input_text="blocked request",
        guardrails=AgentRuntimeGuardrails(
            input=(
                AgentRuntimeGuardrail(
                    name="input-safety",
                    kind="blocked_terms",
                    config={"terms": ["blocked"]},
                ),
            ),
        ),
    )

    async def unexpected_query(**_: object):
        raise AssertionError("blocked input must not reach Claude")
        yield

    with pytest.raises(AgentRuntimeGuardrailBlockedError):
        class UnexpectedClient:
            def __init__(self, options: object) -> None:
                raise AssertionError("blocked input must not create a Claude client")

        asyncio.run(ClaudeAgentSDKRunner(client_factory=UnexpectedClient).run(request))


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
    )
    session.add(profile)
    session.flush()
    task = Task(
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
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=user.id,
            idempotency_key="runtime-controls",
        ),
    )

    assert snapshot["output_schema"] == _output_schema_config()
    assert snapshot["guardrails"] == _guardrail_config()
    assert request.output_schema is not None
    assert request.output_schema.name == "answer"
    assert request.guardrails is not None
    assert request.guardrails.input[0].name == "input-safety"


def test_policy_failure_appends_redacted_durable_event() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run = AgentRun(workspace_id=workspace.id, input={})
    session.add(run)
    session.flush()
    error = AgentRuntimeGuardrailBlockedError(
        AgentRuntimeGuardrailResult(
            stage="input",
            name="input-safety",
            kind="blocked_terms",
            status="blocked",
            blocking=True,
            evidence={"match_count": 1, "api_key": "provider-secret"},
        )
    )

    RunEventRecorder(session).append_model_request_failed_event(run, _request(), error)
    events = list(
        session.scalars(
            select(RunEvent).where(RunEvent.agent_run_id == run.id).order_by(RunEvent.sequence)
        )
    )

    assert [event.event_type for event in events] == [
        "agent.guardrail.blocked",
        "model.request_failed",
    ]
    assert events[0].event_metadata["evidence"] == {
        "match_count": 1,
        "api_key": "[redacted]",
    }


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
    from backend.app.agent_runtime.guardrails import evaluate_guardrail

    with pytest.raises(ValueError):
        evaluate_guardrail(
            AgentRuntimeGuardrail(name="invalid", kind=kind, config=config),
            "safe input",
            stage="input",
        )


def test_agent_profile_rejects_invalid_guardrail_configuration() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)

    with pytest.raises(ValueError, match="unsupported kind"):
        AgentProfileCommandService(session, Settings(environment="test")).create_agent(
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
    )
    values = {
        "agent_profile": profile,
        "input_text": "Return an answer.",
        "context": AgentRuntimeContext(
            workspace_id=workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        "provider": provider,
        "model": model,
        "api_key": "provider-test-key",
        "output_schema": AgentRuntimeOutputSchema(
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
    return AgentRunRequest(**values)


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
