import asyncio
import json
import os
from uuid import uuid4

import pytest
from agents import OpenAIResponsesCompactionSession, RunContextWrapper

import backend.app.agent_runtime.adapters.openai_agents as openai_runtime
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeAgentRef,
    AgentRuntimeApprovalDecision,
    AgentRuntimeCapabilities,
    AgentRuntimeCapability,
    AgentRuntimeContext,
    AgentRuntimeHandoff,
    AgentRuntimeResumeState,
    AgentRuntimeToolContinuation,
    AgentRuntimeToolDefinition,
    AgentRuntimeToolResult,
    AgentRunTracing,
)
from backend.app.agent_runtime.errors import (
    AgentRuntimeCapabilityError,
    normalize_agent_error,
)
from backend.app.agent_runtime.factory import build_agent_runtime_registry
from backend.app.agent_runtime.multi_provider import ProviderAgentRuntimeRegistry
from backend.app.agent_runtime.adapters.openai_agents import OpenAIAgentsRunner
from backend.app.agent_runtime.openai_results import (
    OpenAIAgentsResultMapper,
    runtime_event_from_sdk_item,
)
from backend.app.agent_runtime.openai_tools import OpenAIToolBridge, runtime_allowed_tools
from backend.app.agent_runtime.sessions import PersistentAgentSessionRef, SQLAlchemyAgentSession
from backend.app.agents.models import AgentProfile
from backend.app.model_providers.base_url import normalize_openai_compatible_base_url


class DeterministicTestRunner:
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(final_output="deterministic_test_run_completed")


def test_openai_agents_runner_requires_explicit_provider_api_key() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Researcher",
        role="researcher",
        instructions="Research carefully.",
        model="gpt-4.1",
        model_settings={
            "temperature": 0.2,
            "max_tokens": 500,
            "tool_choice": "required",
            "verbosity": "low",
            "metadata": {"team": "research"},
            "unsupported": {"nested": True},
        },
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Find market trends",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
    )

    with pytest.raises(ValueError, match="explicit provider API key"):
        OpenAIAgentsRunner()._build_agent(request)


def test_openai_agents_runner_does_not_replay_a_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def failing_run(*args: object, **kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(openai_runtime.Runner, "run", failing_run)
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Researcher",
        role="researcher",
        instructions="Research carefully.",
        model="gpt-4.1",
        model_settings={},
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Find market trends",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        provider="openai-compatible",
        api_key="sk-test",
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(OpenAIAgentsRunner().run(request))

    assert attempts == 1


def test_openai_agents_runner_builds_agent_from_explicit_provider() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Researcher",
        role="researcher",
        instructions="Research carefully.",
        model="gpt-4.1",
        model_settings={
            "temperature": 0.2,
            "max_tokens": 500,
            "tool_choice": "required",
            "verbosity": "low",
            "metadata": {"team": "research"},
            "unsupported": {"nested": True},
        },
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Find market trends",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        api_key="sk-test",
        base_url="https://llm.example.test/v1",
    )

    agent = OpenAIAgentsRunner()._build_agent(request)

    assert agent.name == "Researcher"
    assert agent.instructions == "Research carefully."
    assert agent.model.model == "gpt-4.1"
    assert agent.model_settings.temperature == 0.2
    assert agent.model_settings.max_tokens == 500
    assert agent.model_settings.tool_choice == "required"
    assert agent.model_settings.verbosity == "low"
    assert agent.model_settings.metadata == {"team": "research"}
    assert not hasattr(agent.model_settings, "unsupported")


def test_openai_agents_runner_uses_request_provider_override() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Researcher",
        role="researcher",
        instructions="Research carefully.",
        model="workspace-default",
        model_settings={},
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Find market trends",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        model="gpt-4.1-mini",
        base_url="https://llm.example.test/v1",
        api_key="sk-test",
    )

    agent = OpenAIAgentsRunner()._build_agent(request)

    assert agent.model != profile.model
    assert agent.model.model == "gpt-4.1-mini"


def test_openai_agents_runner_can_use_chat_completions_for_compatible_provider() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Researcher",
        role="researcher",
        instructions="Research carefully.",
        model="workspace-default",
        model_settings={},
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Find market trends",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        model="gpt-4.1-mini",
        base_url="https://llm.example.test/v1",
        api_key="sk-test",
        model_api="chat_completions",
    )

    agent = OpenAIAgentsRunner()._build_agent(request)

    assert type(agent.model).__name__ == "OpenAIChatCompletionsModel"
    assert agent.model.model == "gpt-4.1-mini"


def test_openai_agents_runner_normalizes_openai_compatible_base_url() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Researcher",
        role="researcher",
        instructions="Research carefully.",
        model="workspace-default",
        model_settings={},
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Find market trends",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        model="gpt-4.1-mini",
        provider="openai-compatible",
        base_url="https://llm.example.test/",
        api_key="sk-test",
    )

    agent = OpenAIAgentsRunner()._build_agent(request)

    assert agent.model != profile.model
    assert agent.model._client.base_url == "https://llm.example.test/v1/"


def test_openai_agents_runner_uses_only_canonical_model_api_values() -> None:
    assert openai_runtime._use_responses_api("response") is None
    assert openai_runtime._use_responses_api("responses") is True
    assert openai_runtime._use_responses_api("chat") is None
    assert openai_runtime._use_responses_api("chat-completions") is None
    assert openai_runtime._use_responses_api("chat_completions") is False
    assert openai_runtime._use_responses_api("future-api") is None


def test_openai_agents_runner_registers_allowed_mcp_tools() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Designer",
        role="designer",
        instructions="Design carefully.",
        model="gpt-4.1",
        model_settings={},
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Create a poster",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
            allowed_tools=("generate_image", "search.web"),
            tool_definitions=(
                _runtime_tool("generate_image"),
                _runtime_tool("search.web"),
            ),
        ),
        tool_executor=RecordingToolExecutor(),
        api_key="sk-test",
    )

    agent = OpenAIAgentsRunner()._build_agent(request)

    assert [tool.name for tool in agent.tools] == ["generate_image", "search.web"]


def test_openai_agents_runner_renders_tool_continuations_at_runtime_boundary() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Designer",
        role="designer",
        instructions="Design carefully.",
        model="gpt-4.1",
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Continue after the tool result.",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        continuations=(
            AgentRuntimeToolContinuation(
                tool_name="generate_image",
                status="completed",
                result={"asset_id": "img_123"},
            ),
            AgentRuntimeToolContinuation(
                tool_name="search_web",
                status="failed",
                error={"code": "timeout"},
            ),
        ),
    )

    rendered = OpenAIAgentsRunner()._input_for_request(request)

    assert rendered.startswith("Continue after the tool result.")
    assert "Completed runtime tool results:" in rendered
    assert '"tool_name": "generate_image"' in rendered
    assert '"asset_id": "img_123"' in rendered
    assert '"tool_name": "search_web"' in rendered
    assert '"code": "timeout"' in rendered


def test_deterministic_test_runner_returns_deterministic_output() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Fake",
        role="worker",
        instructions="",
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="hello",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
    )

    result = asyncio.run(DeterministicTestRunner().run(request))

    assert result.final_output == "deterministic_test_run_completed"


def test_agent_runner_factory_builds_provider_adapter_registry() -> None:
    assert isinstance(
        build_agent_runtime_registry(),
        ProviderAgentRuntimeRegistry,
    )


def test_provider_adapter_registry_routes_by_request_provider() -> None:
    class RecordingRunner:
        def __init__(self, name: str) -> None:
            self.name = name
            self.requests: list[AgentRunRequest] = []
            self.capabilities = AgentRuntimeCapabilities(
                provider=name,
                adapter=name,
                supported=frozenset(AgentRuntimeCapability),
            )

        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            self.requests.append(request)
            return AgentRunResult(final_output=self.name)

    openai_runner = RecordingRunner("openai")
    anthropic_runner = RecordingRunner("anthropic")
    dispatcher = ProviderAgentRuntimeRegistry(
        adapters={
            "openai-compatible": openai_runner,
            "anthropic": anthropic_runner,
        },
    )
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Claude",
        role="researcher",
        instructions="Think carefully.",
        model="claude-sonnet-4-5",
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="hello",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        provider="anthropic",
    )

    result = asyncio.run(dispatcher.run(request))

    assert result.final_output == "anthropic"
    assert anthropic_runner.requests == [request]
    assert openai_runner.requests == []


def test_provider_adapter_registry_accepts_formal_provider_keys() -> None:
    class RecordingRunner:
        def __init__(self, name: str) -> None:
            self.name = name
            self.requests: list[AgentRunRequest] = []
            self.capabilities = AgentRuntimeCapabilities(
                provider=name,
                adapter=name,
                supported=frozenset(AgentRuntimeCapability),
            )

        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            self.requests.append(request)
            return AgentRunResult(final_output=self.name)

    openai_runner = RecordingRunner("openai")
    anthropic_runner = RecordingRunner("anthropic")
    dispatcher = ProviderAgentRuntimeRegistry(
        adapters={
            "openai-compatible": openai_runner,
            "anthropic": anthropic_runner,
        },
    )
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Mixed Providers",
        role="researcher",
        instructions="Use configured model.",
        model="workspace-default",
    )

    results = [
        asyncio.run(
            dispatcher.run(
                AgentRunRequest(
                    agent_profile=profile,
                    input_text="hello",
                    context=AgentRuntimeContext(
                        workspace_id=profile.workspace_id,
                        task_id=None,
                        run_id=uuid4(),
                    ),
                    provider=provider,
                )
            )
        ).final_output
        for provider in ("anthropic", "openai-compatible")
    ]

    assert results == ["anthropic", "openai"]
    assert len(anthropic_runner.requests) == 1
    assert len(openai_runner.requests) == 1


def test_provider_adapter_registry_rejects_unsupported_request_capabilities() -> None:
    registry = build_agent_runtime_registry()
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Claude",
        role="researcher",
        instructions="Delegate.",
        model="claude-sonnet-4-5",
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Delegate this task.",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        provider="anthropic",
        api_key="anthropic-test",
        handoffs=(AgentRuntimeHandoff(target=AgentRuntimeAgentRef(name="Specialist")),),
    )

    with pytest.raises(AgentRuntimeCapabilityError) as caught:
        asyncio.run(registry.run(request))

    assert caught.value.code == "agent_runtime_capability_unsupported"
    assert caught.value.metadata["provider"] == "anthropic"
    assert caught.value.metadata["missing"] == ["handoffs"]
    assert normalize_agent_error(caught.value).retryable is False

    class Usage:
        def model_dump(self, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {"requests": 1}

    class Result:
        final_output = "done"
        last_response_id = "resp_123"
        conversation_id = "conv_123"
        usage = Usage()

        class last_agent:
            name = "Researcher"

        def to_input_list(self, *, mode: str) -> list[dict[str, object]]:
            assert mode == "normalized"
            return [
                {
                    "role": "assistant",
                    "content": "done api_key=sk-resume-secret",
                    "metadata": {"authorization": "Bearer resume-token"},
                }
            ]

    payload = OpenAIAgentsResultMapper().safe_raw_output(Result())

    assert payload == {
        "final_output": "done",
        "last_response_id": "resp_123",
        "conversation_id": "conv_123",
        "last_agent": "Researcher",
        "resume_input": [
            {
                "role": "assistant",
                "content": "[redacted]",
                "metadata": {"authorization": "[redacted]"},
            }
        ],
        "usage": {"requests": 1},
        "sdk_continuation": {
            "provider": "openai_agents",
            "mode": "sdk_continuation_snapshot",
            "native_tool_call_continuation": False,
            "last_response_id": "resp_123",
            "conversation_id": "conv_123",
            "resume_input": [
                {
                    "role": "assistant",
                    "content": "[redacted]",
                    "metadata": {"authorization": "[redacted]"},
                }
            ],
        },
    }
    serialized = json.dumps(payload)
    assert "sk-resume-secret" not in serialized
    assert "Bearer resume-token" not in serialized


def test_openai_agents_result_mapper_captures_interrupted_state_outside_raw_output() -> None:
    class State:
        def to_json(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["strict_context"] is True
            assert kwargs["include_tracing_api_key"] is False
            return {"$schemaVersion": "1.10", "current_turn": 1}

    class Result:
        interruptions = [object()]

        def to_state(self) -> State:
            return State()

    state = OpenAIAgentsResultMapper().resume_state(Result())

    assert state is not None
    assert state.provider == "openai_agents"
    assert state.schema_version == "1.10"
    assert json.loads(state.serialized_state) == {
        "$schemaVersion": "1.10",
        "current_turn": 1,
    }


def test_openai_agents_runner_restores_sdk_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restored_state = object()
    captured: dict[str, object] = {}

    async def fake_from_json(**kwargs: object) -> object:
        captured["restore"] = kwargs
        return restored_state

    async def fake_runner_run(*args: object, **kwargs: object) -> object:
        captured["run_args"] = args
        captured["run_kwargs"] = kwargs

        class Result:
            final_output = "resumed"
            interruptions: list[object] = []

        return Result()

    monkeypatch.setattr(openai_runtime.RunState, "from_json", fake_from_json)
    monkeypatch.setattr(openai_runtime.Runner, "run", fake_runner_run)
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Resumer",
        role="worker",
        instructions="Resume safely.",
        model="gpt-4.1",
        model_settings={},
    )
    context = AgentRuntimeContext(
        workspace_id=profile.workspace_id,
        task_id=None,
        run_id=uuid4(),
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="This prompt must not replace the state.",
        context=context,
        api_key="sk-test",
        resume_state=AgentRuntimeResumeState(
            provider="openai_agents",
            serialized_state='{"$schemaVersion":"1.10"}',
            schema_version="1.10",
            sdk_version="0.17.2",
        ),
    )

    result = asyncio.run(OpenAIAgentsRunner().run(request))

    assert result.final_output == "resumed"
    assert captured["restore"]["state_json"] == {"$schemaVersion": "1.10"}
    assert captured["restore"]["context_override"].context is context
    assert captured["run_args"][1] is restored_state


def test_openai_agents_runner_applies_approval_to_exact_sdk_interruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved: list[object] = []
    rejected: list[tuple[object, str | None]] = []

    class Interruption:
        call_id = "call-approved"
        name = "write_artifact"

    interruption = Interruption()

    class RejectedInterruption:
        call_id = "call-rejected"
        name = "send_agent_message"

    rejected_interruption = RejectedInterruption()

    class State:
        def get_interruptions(self) -> list[object]:
            return [interruption, rejected_interruption]

        def approve(self, item: object) -> None:
            approved.append(item)

        def reject(self, item: object, *, rejection_message: str | None = None) -> None:
            rejected.append((item, rejection_message))

    state = State()

    async def fake_from_json(**_: object) -> State:
        return state

    async def fake_runner_run(*_: object, **__: object) -> object:
        class Result:
            final_output = "resumed"
            interruptions: list[object] = []

        return Result()

    monkeypatch.setattr(openai_runtime.RunState, "from_json", fake_from_json)
    monkeypatch.setattr(openai_runtime.Runner, "run", fake_runner_run)
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Resumer",
        role="worker",
        instructions="Resume safely.",
        model="gpt-4.1",
        model_settings={},
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="ignored",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        api_key="sk-test",
        resume_state=AgentRuntimeResumeState(
            provider="openai_agents",
            serialized_state='{"$schemaVersion":"1.10"}',
        ),
        approval_decisions=(
            AgentRuntimeApprovalDecision(
                tool_call_id="call-approved",
                tool_name="write_artifact",
                status="approved",
            ),
            AgentRuntimeApprovalDecision(
                tool_call_id="call-rejected",
                tool_name="send_agent_message",
                status="rejected",
                reason="operator denied",
            ),
        ),
    )

    result = asyncio.run(OpenAIAgentsRunner().run(request))

    assert result.final_output == "resumed"
    assert approved == [interruption]
    assert rejected == [(rejected_interruption, "operator denied")]


def test_openai_agents_runner_wraps_persistent_session_with_native_compaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        final_output = "done"

    captured: dict[str, object] = {}

    async def fake_runner_run(*args: object, **kwargs: object) -> Result:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return Result()

    class RecordingSession:
        session_id = "workspace:team_agent:team-1:agent-1"
        session_settings = None

        async def get_items(self, limit: int | None = None) -> list[dict[str, object]]:
            return []

        async def add_items(self, items: list[dict[str, object]]) -> None:
            return None

        async def pop_item(self) -> dict[str, object] | None:
            return None

        async def clear_session(self) -> None:
            return None

    monkeypatch.setattr(openai_runtime.Runner, "run", fake_runner_run)
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Researcher",
        role="researcher",
        instructions="Research carefully.",
        model="gpt-4.1",
        model_settings={},
    )
    session = RecordingSession()
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Continue the company research.",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        session=session,
        previous_response_id="resp_previous",
        conversation_id="conv_123",
        api_key="sk-test",
    )

    result = asyncio.run(OpenAIAgentsRunner().run(request))

    assert result.final_output == "done"
    hooks = captured["kwargs"].pop("hooks")
    assert hooks.__class__.__name__ == "OpenAIRuntimeHooks"
    sdk_session = captured["kwargs"].pop("session")
    assert isinstance(sdk_session, OpenAIResponsesCompactionSession)
    assert sdk_session.underlying_session.session_id == session.session_id
    assert asyncio.run(sdk_session.underlying_session.get_items()) == []
    assert sdk_session.model == "gpt-4.1"
    assert captured["kwargs"] == {
        "context": request.context,
        "max_turns": 10,
        "run_config": None,
        "previous_response_id": "resp_previous",
        "conversation_id": "conv_123",
    }


def test_openai_agents_runner_passes_tracing_run_config_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        final_output = "done"

    captured: dict[str, object] = {}

    async def fake_runner_run(*args: object, **kwargs: object) -> Result:
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(openai_runtime.Runner, "run", fake_runner_run)
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Researcher",
        role="researcher",
        instructions="Research carefully.",
        model="gpt-4.1",
        model_settings={},
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Trace this.",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        tracing=AgentRunTracing(
            workflow_name="opsmesh.team_agent_run",
            trace_id="trace_123",
            group_id="workspace:team_agent:team-1:agent-1",
            metadata={
                "team": {"team_id": "team-1"},
                "persistent_session_key": "workspace:team_agent:team-1:agent-1",
            },
        ),
        api_key="sk-test",
    )

    result = asyncio.run(OpenAIAgentsRunner().run(request))
    run_config = captured["kwargs"]["run_config"]

    assert result.final_output == "done"
    assert run_config.workflow_name == "opsmesh.team_agent_run"
    assert run_config.trace_id == "trace_123"
    assert run_config.group_id == "workspace:team_agent:team-1:agent-1"
    assert run_config.trace_metadata == {
        "team": {"team_id": "team-1"},
        "persistent_session_key": "workspace:team_agent:team-1:agent-1",
    }


def test_openai_agents_runner_trace_metadata_includes_provider_provenance() -> None:
    credential_id = uuid4()
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Provider Runner",
        role="tester",
        instructions="Trace provider provenance.",
        model="gpt-4.1",
        model_settings={},
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Trace this.",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        tracing=AgentRunTracing(
            workflow_name="opsmesh.agent_run",
            trace_id="trace_provider",
            group_id="workspace:provider",
            metadata={
                "run_model": "gpt-4.1-mini",
                "model_provider_provider": "openai-compatible",
                "model_provider_credential_id": str(credential_id),
                "model_provider_model_api": "responses",
            },
        ),
    )

    run_config = OpenAIAgentsRunner()._run_config(request)

    assert run_config is not None
    assert run_config.trace_metadata["run_model"] == "gpt-4.1-mini"
    assert run_config.trace_metadata["model_provider_provider"] == "openai-compatible"
    assert run_config.trace_metadata["model_provider_credential_id"] == str(credential_id)
    assert run_config.trace_metadata["model_provider_model_api"] == "responses"
    serialized = json.dumps(run_config.trace_metadata)
    assert "api_key" not in serialized
    assert "base_url" not in serialized


def test_openai_agents_runner_runtime_event_payload_is_redacted() -> None:
    class SdkEvent:
        type = "response.output_text.delta"
        message = "delta"
        api_key = "sk-event-secret"

        def model_dump(self, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {
                "type": self.type,
                "message": self.message,
                "api_key": self.api_key,
                "nested": {
                    "base_url": "https://event.example.test/v1/private",
                    "visible": "ok",
                },
            }

    event = runtime_event_from_sdk_item(SdkEvent())

    assert event is not None
    assert event.payload == {
        "type": "response.output_text.delta",
        "message": "delta",
        "api_key": "[redacted]",
        "nested": {"base_url": "[redacted]", "visible": "ok"},
    }
    serialized = json.dumps(event.payload)
    assert "sk-event-secret" not in serialized
    assert "event.example.test/v1/private" not in serialized


def test_openai_agents_runner_tools_include_provenance_guardrail() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Designer",
        role="designer",
        instructions="Design carefully.",
        model="gpt-4.1",
        model_settings={},
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Create a poster",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
            allowed_tools=("generate_image",),
            tool_definitions=(_runtime_tool("generate_image"),),
        ),
        tool_executor=RecordingToolExecutor(),
        api_key="sk-test",
    )

    tool = OpenAIAgentsRunner()._build_agent(request).tools[0]

    assert tool.description == "Execute generate_image."
    assert tool.params_json_schema == _runtime_tool("generate_image").input_schema
    assert tool.tool_input_guardrails is not None
    assert tool.tool_input_guardrails[0].name == ("generate_image:runtime_allowed_tool_provenance")


def test_openai_tool_bridge_maps_dynamic_sdk_approval_interruption() -> None:
    calls: list[str] = []

    class Executor:
        def review_tool_call(self, **_: object) -> dict[str, object]:
            return {
                "decision": "require_approval",
                "risk_level": "high",
                "reasons": ["tool.arguments.sensitive"],
            }

        async def execute_tool(
            self,
            *,
            tool_call_id: str,
            **_: object,
        ) -> AgentRuntimeToolResult:
            calls.append(tool_call_id)
            return AgentRuntimeToolResult(status="completed", output={"ok": True})

    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Approver",
        role="worker",
        instructions="Use tools.",
        model="gpt-4.1",
    )
    context = AgentRuntimeContext(
        workspace_id=profile.workspace_id,
        task_id=None,
        run_id=uuid4(),
        allowed_tools=("write_artifact",),
        tool_definitions=(
            AgentRuntimeToolDefinition(
                name="write_artifact",
                source="product",
                description="Write an artifact.",
                input_schema={"type": "object"},
                requires_approval=True,
                risk_level="high",
            ),
        ),
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="write",
        context=context,
        tool_executor=Executor(),
    )
    tool = OpenAIToolBridge().tools(request)[0]

    assert callable(tool.needs_approval)
    required = asyncio.run(
        tool.needs_approval(
            RunContextWrapper(context=context),
            {"content": "private"},
            "call-dynamic",
        )
    )

    item = type("Item", (), {})()
    item.call_id = "call-dynamic"
    item.name = "write_artifact"
    item.arguments = '{"content":"private"}'
    item.agent = openai_runtime.Agent(name="Approver", tools=[tool])
    result = type("Result", (), {"interruptions": [item]})()

    interruptions = OpenAIAgentsResultMapper().interruptions(result)
    tool_context = type(
        "ToolContextStub",
        (),
        {"context": context, "tool_call_id": "call-dynamic"},
    )()
    output = asyncio.run(tool.on_invoke_tool(tool_context, item.arguments))

    assert required is True
    assert interruptions[0].tool_call_id == "call-dynamic"
    assert interruptions[0].tool_kind == "product"
    assert interruptions[0].arguments == {"content": "private"}
    assert interruptions[0].policy_decision["risk_level"] == "high"
    assert output["ok"] is True
    assert calls == ["call-dynamic"]


@pytest.mark.parametrize("decision", [None, "unknown", "deny"])
def test_openai_tool_bridge_denies_non_allowing_policy_decisions(decision: str | None) -> None:
    class Executor:
        def review_tool_call(self, **_: object) -> dict[str, object]:
            return {"decision": decision}

        async def execute_tool(self, **_: object) -> AgentRuntimeToolResult:
            raise AssertionError("Denied tool must not execute")

    context = AgentRuntimeContext(workspace_id=uuid4(), task_id=None, run_id=uuid4())
    tool = OpenAIToolBridge().function_tool(
        AgentRuntimeToolDefinition(
            name="write_artifact", source="product", description="Write",
            input_schema={"type": "object"},
        ),
        Executor(),
        runtime_context=context,
    )
    assert callable(tool.needs_approval)
    with pytest.raises(ValueError):
        asyncio.run(tool.needs_approval(RunContextWrapper(context=context), {}, "denied-call"))


def test_openai_agents_runner_tool_provenance_accepts_restored_mapping_context() -> None:
    assert runtime_allowed_tools({"allowed_tools": ["generate_image", "write_artifact"]}) == (
        "generate_image",
        "write_artifact",
    )


@pytest.mark.openai_smoke
@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY is required for real OpenAI SDK smoke tests",
)
def test_openai_agents_runner_real_sdk_smoke_preserves_boundary_configuration() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Smoke Runner",
        role="tester",
        instructions=("Return exactly: smoke-ok. Do not call tools unless the user asks for one."),
        model=os.getenv("OPENAI_SMOKE_MODEL", "gpt-4.1-nano"),
        model_settings={"temperature": 0, "store": False, "include_usage": True},
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Reply with exactly smoke-ok.",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
            allowed_tools=("opsmesh_echo",),
            tool_definitions=(_runtime_tool("opsmesh_echo"),),
            metadata={"smoke": "openai_agents_runner"},
        ),
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=_openai_smoke_base_url(),
        model_api=_openai_smoke_model_api(),
        tool_executor=RecordingToolExecutor(),
        tracing=AgentRunTracing(
            workflow_name="opsmesh.openai_sdk_smoke",
            trace_id=None,
            group_id="openai-sdk-smoke",
            metadata={
                "smoke": "openai_agents_runner",
                "persistent_session_key": "smoke-session-key",
            },
            disabled=True,
        ),
    )

    result = asyncio.run(OpenAIAgentsRunner().run(request))
    run_config = OpenAIAgentsRunner()._run_config(request)
    agent = OpenAIAgentsRunner()._build_agent(request)

    assert result.final_output
    assert run_config is not None
    assert run_config.workflow_name == "opsmesh.openai_sdk_smoke"
    assert run_config.group_id == "openai-sdk-smoke"
    assert run_config.trace_metadata["persistent_session_key"] == "smoke-session-key"
    assert run_config.tracing_disabled is True
    assert run_config.trace_include_sensitive_data is False
    assert [tool.name for tool in agent.tools] == ["opsmesh_echo"]
    assert request.context.metadata["smoke"] == "openai_agents_runner"


@pytest.mark.openai_smoke
@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY is required for real OpenAI SDK smoke tests",
)
def test_openai_agents_runner_real_sdk_smoke_uses_tool_and_persistent_session() -> None:
    from backend.tests.test_agent_runtime_sessions import _add_workspace, _session

    session = _session()
    workspace = _add_workspace(session)
    profile = AgentProfile(
        workspace_id=workspace.id,
        name="Tool Smoke Runner",
        role="tester",
        instructions=(
            "You must call the opsmesh_echo tool exactly once. "
            "Then reply with the value from the tool result."
        ),
        model=os.getenv("OPENAI_SMOKE_MODEL", "gpt-4.1-nano"),
        model_settings={
            "temperature": 0,
            "store": False,
            "include_usage": True,
            "parallel_tool_calls": False,
            "tool_choice": "opsmesh_echo",
        },
    )
    run_id = uuid4()
    persistent_session = SQLAlchemyAgentSession(
        db_session=session,
        ref=PersistentAgentSessionRef(
            session_key=f"{workspace.id}:openai-smoke:{run_id}",
            workspace_id=workspace.id,
            scope_type="openai_smoke",
            scope_id=str(run_id),
        ),
        agent_profile_id=profile.id,
        metadata={"source": "openai_smoke"},
    )
    executor = RecordingToolExecutor(output={"echo": "persistent-tool-ok"})
    request = AgentRunRequest(
        agent_profile=profile,
        input_text=(
            "Call opsmesh_echo with any JSON object. "
            "After the tool call, answer exactly persistent-tool-ok."
        ),
        context=AgentRuntimeContext(
            workspace_id=workspace.id,
            task_id=None,
            run_id=run_id,
            allowed_tools=("opsmesh_echo",),
            tool_definitions=(_runtime_tool("opsmesh_echo"),),
            metadata={"persistent_session_key": persistent_session.session_id},
        ),
        max_turns=4,
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=_openai_smoke_base_url(),
        model_api=_openai_smoke_model_api(),
        tool_executor=executor,
        session=persistent_session,
        tracing=AgentRunTracing(
            workflow_name="opsmesh.openai_sdk_tool_session_smoke",
            group_id=persistent_session.session_id,
            metadata={
                "smoke": "openai_agents_tool_session",
                "persistent_session_key": persistent_session.session_id,
            },
            disabled=True,
        ),
    )

    result = asyncio.run(OpenAIAgentsRunner().run(request))
    stored_items = asyncio.run(persistent_session.get_items())

    assert executor.calls == ["opsmesh_echo"]
    assert "persistent-tool-ok" in result.final_output
    assert stored_items
    assert request.base_url == _openai_smoke_base_url()


def test_openai_agents_runner_real_sdk_smoke_skips_without_api_key() -> None:
    marker = pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY is required for real OpenAI SDK smoke tests",
    )

    assert marker.mark.args == (not os.getenv("OPENAI_API_KEY"),)
    assert "OPENAI_API_KEY" in marker.mark.kwargs["reason"]


def test_openai_agents_runner_smoke_base_url_uses_smoke_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://default.example.test/v1")
    monkeypatch.setenv("OPENAI_SMOKE_BASE_URL", "https://smoke.example.test/v1")

    assert _openai_smoke_base_url() == "https://smoke.example.test/v1"


def test_openai_agents_runner_smoke_base_url_normalizes_root_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_SMOKE_BASE_URL", "https://dash.ovload.com/")

    assert _openai_smoke_base_url() == "https://dash.ovload.com/v1"


def test_openai_agents_runner_smoke_model_api_uses_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_SMOKE_MODEL_API", "chat_completions")

    assert _openai_smoke_model_api() == "chat_completions"


def test_agent_error_normalization_is_safe_for_persistence() -> None:
    error = normalize_agent_error(RuntimeError("network unavailable"))

    assert error.as_dict() == {
        "code": "RuntimeError",
        "message": "network unavailable",
        "retryable": True,
    }


def test_agent_error_normalization_redacts_provider_secrets() -> None:
    error = normalize_agent_error(
        RuntimeError(
            "provider rejected api_key=sk-provider-secret "
            "Bearer bearer-secret-token "
            "base_url=https://provider.example.test/v1/private"
        )
    )

    assert error.code == "RuntimeError"
    assert error.message == "[redacted]"
    serialized = json.dumps(error.as_dict())
    assert "sk-provider-secret" not in serialized
    assert "Bearer bearer-secret-token" not in serialized
    assert "base_url" not in serialized
    assert "https://provider.example.test/v1/private" not in serialized


def _runtime_tool(name: str) -> AgentRuntimeToolDefinition:
    return AgentRuntimeToolDefinition(
        name=name,
        source="mcp",
        description=f"Execute {name}.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": True,
        },
    )


class RecordingToolExecutor:
    def __init__(self, output: dict[str, object] | None = None) -> None:
        self.output = output or {"ok": True}
        self.calls: list[str] = []

    def review_tool_call(self, **_: object) -> dict[str, object]:
        return {"decision": "allow", "risk_level": "low", "reasons": []}

    async def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        tool_call_id: str,
        approval_granted: bool,
    ) -> AgentRuntimeToolResult:
        self.calls.append(tool_name)
        return AgentRuntimeToolResult(
            status="completed",
            output=self.output,
            metadata={"executor": "recording"},
        )


def _openai_smoke_base_url() -> str | None:
    return normalize_openai_compatible_base_url(
        os.getenv("OPENAI_SMOKE_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    )


def _openai_smoke_model_api() -> str | None:
    return os.getenv("OPENAI_SMOKE_MODEL_API")
