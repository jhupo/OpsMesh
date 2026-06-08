import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest

import backend.app.agent_runtime.anthropic as anthropic_runtime
import backend.app.agent_runtime.openai_agents as openai_runtime
from backend.app.agent_runtime.anthropic import AnthropicMessagesRunner
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeContext,
    AgentRuntimeToolContinuation,
    AgentRuntimeToolResult,
    AgentRunTracing,
)
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.agent_runtime.factory import build_agent_runner
from backend.app.agent_runtime.multi_provider import ProviderDispatchingAgentRunner
from backend.app.agent_runtime.openai_agents import OpenAIAgentsRunner
from backend.app.agent_runtime.sessions import PersistentAgentSessionRef, SQLAlchemyAgentSession
from backend.app.agents.models import AgentProfile
from backend.app.core.config import Settings
from backend.app.model_providers.base_url import normalize_openai_compatible_base_url


class DeterministicTestRunner:
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(final_output="deterministic_test_run_completed")


def test_openai_agents_runner_builds_agent_from_profile() -> None:
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

    agent = OpenAIAgentsRunner()._build_agent(request)

    assert agent.name == "Researcher"
    assert agent.instructions == "Research carefully."
    assert agent.model == "gpt-4.1"
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

    circuit_key = openai_runtime._model_provider_circuit_key(request)
    agent = OpenAIAgentsRunner()._build_agent(request)

    assert agent.model != profile.model
    assert agent.model._client.base_url == "https://llm.example.test/v1/"
    assert circuit_key.startswith("model-provider:openai-compatible:llm.example.test:")


def test_openai_agents_runner_circuit_key_separates_provider_aliases() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Researcher",
        role="researcher",
        instructions="Research carefully.",
        model="gpt-4.1-mini",
        model_settings={},
    )
    context = AgentRuntimeContext(
        workspace_id=profile.workspace_id,
        task_id=None,
        run_id=uuid4(),
    )
    openai_request = AgentRunRequest(
        agent_profile=profile,
        input_text="Find market trends",
        context=context,
        model="gpt-4.1-mini",
        provider="openai",
        base_url="https://llm.example.test/v1",
        api_key="sk-test",
    )
    compatible_request = AgentRunRequest(
        agent_profile=profile,
        input_text="Find market trends",
        context=context,
        model="gpt-4.1-mini",
        provider="openai-compatible",
        base_url="https://llm.example.test/v1",
        api_key="sk-test",
    )

    assert openai_runtime._model_provider_circuit_key(openai_request).startswith(
        "model-provider:openai:"
    )
    assert openai_runtime._model_provider_circuit_key(compatible_request).startswith(
        "model-provider:openai-compatible:"
    )
    assert openai_runtime._model_provider_circuit_key(
        openai_request
    ) != openai_runtime._model_provider_circuit_key(compatible_request)


def test_openai_agents_runner_canonicalizes_model_api_aliases() -> None:
    assert openai_runtime._use_responses_api("response") is True
    assert openai_runtime._use_responses_api("responses") is True
    assert openai_runtime._use_responses_api("chat") is False
    assert openai_runtime._use_responses_api("chat-completions") is False
    assert openai_runtime._use_responses_api("chat_completions") is False
    assert openai_runtime._use_responses_api("future-api") is None


def test_openai_agents_runner_circuit_key_canonicalizes_model_api_aliases() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Researcher",
        role="researcher",
        instructions="Research carefully.",
        model="gpt-4.1-mini",
        model_settings={},
    )
    context = AgentRuntimeContext(
        workspace_id=profile.workspace_id,
        task_id=None,
        run_id=uuid4(),
    )
    chat_dash_request = AgentRunRequest(
        agent_profile=profile,
        input_text="Find market trends",
        context=context,
        model="gpt-4.1-mini",
        provider="openai-compatible",
        base_url="https://llm.example.test/v1",
        api_key="sk-test",
        model_api="chat-completions",
    )
    chat_underscore_request = AgentRunRequest(
        agent_profile=profile,
        input_text="Find market trends",
        context=context,
        model="gpt-4.1-mini",
        provider="openai-compatible",
        base_url="https://llm.example.test/v1",
        api_key="sk-test",
        model_api="chat_completions",
    )

    assert openai_runtime._model_provider_circuit_key(
        chat_dash_request
    ) == openai_runtime._model_provider_circuit_key(chat_underscore_request)
    assert ":chat_completions:" in openai_runtime._model_provider_circuit_key(
        chat_dash_request
    )


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
        ),
        tool_executor=RecordingToolExecutor(),
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


def test_agent_runner_factory_builds_real_provider_dispatching_runner() -> None:
    assert isinstance(
        build_agent_runner(Settings(environment="test")),
        ProviderDispatchingAgentRunner,
    )


def test_provider_dispatching_runner_routes_by_request_provider() -> None:
    class RecordingRunner:
        def __init__(self, name: str) -> None:
            self.name = name
            self.requests: list[AgentRunRequest] = []

        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            self.requests.append(request)
            return AgentRunResult(final_output=self.name)

    openai_runner = RecordingRunner("openai")
    anthropic_runner = RecordingRunner("anthropic")
    dispatcher = ProviderDispatchingAgentRunner(
        openai_runner=openai_runner,
        anthropic_runner=anthropic_runner,
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


def test_provider_dispatching_runner_accepts_provider_aliases() -> None:
    class RecordingRunner:
        def __init__(self, name: str) -> None:
            self.name = name
            self.requests: list[AgentRunRequest] = []

        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            self.requests.append(request)
            return AgentRunResult(final_output=self.name)

    openai_runner = RecordingRunner("openai")
    anthropic_runner = RecordingRunner("anthropic")
    dispatcher = ProviderDispatchingAgentRunner(
        openai_runner=openai_runner,
        anthropic_runner=anthropic_runner,
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
        for provider in (" Claude API ", "OpenAI Compatible Gateway")
    ]

    assert results == ["anthropic", "openai"]
    assert len(anthropic_runner.requests) == 1
    assert len(openai_runner.requests) == 1


def test_anthropic_runner_circuit_key_canonicalizes_provider_and_separates_hosts() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Claude",
        role="researcher",
        instructions="Research.",
        model="claude-sonnet-4-6",
    )
    context = AgentRuntimeContext(
        workspace_id=profile.workspace_id,
        task_id=None,
        run_id=uuid4(),
    )
    alias_request = AgentRunRequest(
        agent_profile=profile,
        input_text="Summarize.",
        context=context,
        provider="Claude API",
        base_url="https://api.anthropic.com/private",
        api_key="anthropic-key",
    )
    canonical_request = AgentRunRequest(
        agent_profile=profile,
        input_text="Summarize.",
        context=context,
        provider="anthropic",
        base_url="https://api.anthropic.com/v1",
        api_key="anthropic-key",
    )
    router_request = AgentRunRequest(
        agent_profile=profile,
        input_text="Summarize.",
        context=context,
        provider="anthropic",
        base_url="https://claude-router.example.test/private",
        api_key="anthropic-key",
    )

    alias_key = anthropic_runtime._model_provider_circuit_key(alias_request)
    canonical_key = anthropic_runtime._model_provider_circuit_key(canonical_request)
    router_key = anthropic_runtime._model_provider_circuit_key(router_request)

    assert alias_key == canonical_key
    assert alias_key.startswith(
        "model-provider:anthropic:api.anthropic.com:no-credential:"
        "anthropic_messages:claude-sonnet-4-6"
    )
    assert router_key != alias_key
    assert "private" not in alias_key
    assert "private" not in router_key


def test_anthropic_messages_runner_sends_native_messages_request() -> None:
    calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "json": json_from_request(request),
            }
        )
        return httpx.Response(
            200,
            json={
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "claude-ok"}],
                "usage": {"input_tokens": 10, "output_tokens": 3},
                "metadata": {
                    "api_key": "anthropic-key",
                    "base_url": "https://api.anthropic.com/private",
                    "note": "visible",
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Claude",
        role="researcher",
        instructions="Use careful reasoning.",
        model="claude-sonnet-4-5",
        model_settings={"temperature": 0.1, "max_tokens": 256},
    )
    credential_id = uuid4()
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Summarize.",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        provider="anthropic",
        model="claude-opus-4-6",
        api_key="anthropic-key",
        base_url="https://api.anthropic.com",
        model_api="responses",
        model_provider_credential_id=credential_id,
    )

    result = asyncio.run(AnthropicMessagesRunner(client=client).run(request))
    asyncio.run(client.aclose())

    assert result.final_output == "claude-ok"
    assert result.events[0].event_type == "model.usage"
    assert calls[0]["url"] == "https://api.anthropic.com/v1/messages"
    assert calls[0]["headers"]["x-api-key"] == "anthropic-key"
    assert calls[0]["json"]["model"] == "claude-opus-4-6"
    assert calls[0]["json"]["system"] == "Use careful reasoning."
    assert calls[0]["json"]["max_tokens"] == 256
    assert calls[0]["json"]["temperature"] == 0.1
    assert result.raw_output == {
        "provider": "anthropic",
        "model": "claude-opus-4-6",
        "model_api": "anthropic_messages",
        "model_provider_credential_id": str(credential_id),
        "trace": None,
        "response": {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "claude-ok"}],
            "usage": {"input_tokens": 10, "output_tokens": 3},
            "metadata": {
                "api_key": "[redacted]",
                "base_url": "[redacted]",
                "note": "visible",
            },
        },
    }
    assert result.events[1].event_type == "model.request"
    assert result.events[1].payload == {
        "model_provider": {
            "provider": "anthropic",
            "model": "claude-opus-4-6",
            "model_api": "anthropic_messages",
            "credential_id": str(credential_id),
        },
        "trace": None,
    }
    assert "anthropic-key" not in str(result.raw_output)
    assert "api.anthropic.com" not in str(result.raw_output)


def test_anthropic_messages_runner_executes_tool_use_loop() -> None:
    calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json_from_request(request)
        calls.append(payload)
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "id": "msg_tool",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_123",
                            "name": "search_docs",
                            "input": {"query": "runtime"},
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "msg_final",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "tool-result-ok"}],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Claude",
        role="researcher",
        instructions="Use tools.",
        model="claude-sonnet-4-5",
        model_settings={"tool_choice": "search_docs"},
    )
    executor = RecordingToolExecutor(output={"answer": "tool-result-ok"})
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Search docs.",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
            allowed_tools=("search_docs",),
        ),
        provider="anthropic",
        api_key="anthropic-key",
        tool_executor=executor,
    )

    result = asyncio.run(AnthropicMessagesRunner(client=client).run(request))
    asyncio.run(client.aclose())

    assert result.final_output == "tool-result-ok"
    assert executor.calls == ["search_docs"]
    assert calls[0]["tools"][0]["name"] == "search_docs"
    assert calls[0]["tool_choice"] == {"type": "tool", "name": "search_docs"}
    assert calls[1]["messages"][1]["content"][0]["name"] == "search_docs"
    assert calls[1]["messages"][2]["content"][0]["tool_use_id"] == "toolu_123"
    assert result.events[0].payload == {
        "tool_name": "search_docs",
        "tool_call_id": "toolu_123",
        "status": "completed",
        "metadata": {"executor": "recording"},
    }


def test_anthropic_messages_runner_records_trace_and_tool_provenance() -> None:
    calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json_from_request(request)
        calls.append(payload)
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "id": "msg_tool",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_trace",
                            "name": "search_docs",
                            "input": {"query": "provenance"},
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "msg_final",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "trace-ok"}],
                "metadata": {"api_key": "anthropic-response-secret"},
            },
        )

    credential_id = uuid4()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Claude Trace",
        role="researcher",
        instructions="Trace tools.",
        model="claude-sonnet-4-5",
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Search docs.",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
            allowed_tools=("search_docs",),
        ),
        provider="anthropic",
        api_key="anthropic-key",
        base_url="https://api.anthropic.com/private",
        model_provider_credential_id=credential_id,
        tool_executor=RecordingToolExecutor(),
        tracing=AgentRunTracing(
            workflow_name="chaincloud.team_agent_run",
            trace_id="trace_anthropic",
            group_id="workspace:team_agent:team-1:agent-1",
            metadata={
                "team": {"team_id": "team-1"},
                "persistent_session_key": "workspace:team_agent:team-1:agent-1",
                "api_key": "sk-trace-secret",
                "base_url": "https://trace.example.test/private",
            },
            disabled=True,
        ),
    )

    result = asyncio.run(AnthropicMessagesRunner(client=client).run(request))
    asyncio.run(client.aclose())

    assert result.final_output == "trace-ok"
    assert result.raw_output["trace"] == {
        "workflow_name": "chaincloud.team_agent_run",
        "trace_id": "trace_anthropic",
        "group_id": "workspace:team_agent:team-1:agent-1",
        "metadata": {
            "team": {"team_id": "team-1"},
            "persistent_session_key": "workspace:team_agent:team-1:agent-1",
            "api_key": "[redacted]",
            "base_url": "[redacted]",
        },
        "disabled": True,
        "include_sensitive_data": False,
    }
    assert result.raw_output["model_provider_credential_id"] == str(credential_id)
    assert result.events[0].event_type == "tool.completed"
    assert result.events[0].payload["metadata"] == {"executor": "recording"}
    assert result.events[1].event_type == "model.request"
    assert result.events[1].payload == {
        "model_provider": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-5",
            "model_api": "anthropic_messages",
            "credential_id": str(credential_id),
        },
        "trace": {
            "workflow_name": "chaincloud.team_agent_run",
            "trace_id": "trace_anthropic",
            "group_id": "workspace:team_agent:team-1:agent-1",
            "metadata": {
                "team": {"team_id": "team-1"},
                "persistent_session_key": "workspace:team_agent:team-1:agent-1",
                "api_key": "[redacted]",
                "base_url": "[redacted]",
            },
            "disabled": True,
        },
    }
    serialized = json.dumps(result.raw_output)
    assert "sk-trace-secret" not in serialized
    assert "trace.example.test/private" not in serialized
    assert "anthropic-response-secret" not in serialized


def test_anthropic_messages_runner_uses_persistent_session_history() -> None:
    calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json_from_request(request)
        calls.append(payload)
        return httpx.Response(
            200,
            json={
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "continued"}],
            },
        )

    class RecordingSession:
        session_id = "workspace:team_agent:team-1:claude"
        session_settings = None

        def __init__(self) -> None:
            self.added: list[dict[str, object]] = []

        async def get_items(self, limit: int | None = None) -> list[dict[str, object]]:
            return [
                {"role": "user", "content": "previous question"},
                {"role": "assistant", "content": "previous answer"},
            ]

        async def add_items(self, items: list[dict[str, object]]) -> None:
            self.added.extend(items)

        async def pop_item(self) -> dict[str, object] | None:
            return None

        async def clear_session(self) -> None:
            return None

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    session = RecordingSession()
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Claude",
        role="researcher",
        instructions="Continue.",
        model="claude-sonnet-4-5",
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="next question",
        context=AgentRuntimeContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        provider="anthropic",
        api_key="anthropic-key",
        session=session,
    )

    result = asyncio.run(AnthropicMessagesRunner(client=client).run(request))
    asyncio.run(client.aclose())

    assert result.final_output == "continued"
    assert calls[0]["messages"] == [
        {"role": "user", "content": "previous question"},
        {"role": "assistant", "content": "previous answer"},
        {"role": "user", "content": "next question"},
    ]
    assert session.added == [
        {"role": "user", "content": "next question"},
        {"role": "assistant", "content": "continued"},
    ]


def test_openai_agents_runner_raw_output_is_json_safe() -> None:
    class Usage:
        def model_dump(self, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {"requests": 1}

    class State:
        def to_json(self) -> str:
            return '{"schema_version":"1.10","api_key":"sk-state-secret"}'

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

        def to_state(self) -> State:
            return State()

    payload = OpenAIAgentsRunner()._safe_raw_output(Result())

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
        "run_state_json": "[redacted]",
        "usage": {"requests": 1},
        "sdk_continuation": {
            "provider": "openai_agents",
            "mode": "runner_level_fallback",
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
            "run_state_json": "[redacted]",
        },
    }
    serialized = json.dumps(payload)
    assert "sk-resume-secret" not in serialized
    assert "Bearer resume-token" not in serialized
    assert "sk-state-secret" not in serialized


def test_openai_agents_runner_passes_persistent_session_to_sdk(
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
    )

    result = asyncio.run(OpenAIAgentsRunner().run(request))

    assert result.final_output == "done"
    assert captured["kwargs"] == {
        "context": request.context,
        "max_turns": 10,
        "run_config": None,
        "previous_response_id": "resp_previous",
        "conversation_id": "conv_123",
        "session": session,
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
            workflow_name="chaincloud.team_agent_run",
            trace_id="trace_123",
            group_id="workspace:team_agent:team-1:agent-1",
            metadata={
                "team": {"team_id": "team-1"},
                "persistent_session_key": "workspace:team_agent:team-1:agent-1",
            },
        ),
    )

    result = asyncio.run(OpenAIAgentsRunner().run(request))
    run_config = captured["kwargs"]["run_config"]

    assert result.final_output == "done"
    assert run_config.workflow_name == "chaincloud.team_agent_run"
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
            workflow_name="chaincloud.agent_run",
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

    event = OpenAIAgentsRunner()._runtime_event_from_sdk_item(SdkEvent())

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
        ),
        tool_executor=RecordingToolExecutor(),
    )

    tool = OpenAIAgentsRunner()._build_agent(request).tools[0]

    assert "ctx" not in tool.params_json_schema["properties"]
    assert "arguments" in tool.params_json_schema["properties"]
    assert tool.tool_input_guardrails is not None
    assert tool.tool_input_guardrails[0].name == (
        "generate_image:runtime_allowed_tool_provenance"
    )


def test_openai_agents_runner_tool_provenance_accepts_restored_mapping_context() -> None:
    assert openai_runtime._runtime_allowed_tools(
        {"allowed_tools": ["generate_image", "write_artifact"]}
    ) == ("generate_image", "write_artifact")


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
        instructions=(
            "Return exactly: smoke-ok. Do not call tools unless the user asks for one."
        ),
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
            allowed_tools=("chaincloud_echo",),
            metadata={"smoke": "openai_agents_runner"},
        ),
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=_openai_smoke_base_url(),
        model_api=_openai_smoke_model_api(),
        tool_executor=RecordingToolExecutor(),
        tracing=AgentRunTracing(
            workflow_name="chaincloud.openai_sdk_smoke",
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
    assert run_config.workflow_name == "chaincloud.openai_sdk_smoke"
    assert run_config.group_id == "openai-sdk-smoke"
    assert run_config.trace_metadata["persistent_session_key"] == "smoke-session-key"
    assert run_config.tracing_disabled is True
    assert run_config.trace_include_sensitive_data is False
    assert [tool.name for tool in agent.tools] == ["chaincloud_echo"]
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
            "You must call the chaincloud_echo tool exactly once. "
            "Then reply with the value from the tool result."
        ),
        model=os.getenv("OPENAI_SMOKE_MODEL", "gpt-4.1-nano"),
        model_settings={
            "temperature": 0,
            "store": False,
            "include_usage": True,
            "parallel_tool_calls": False,
            "tool_choice": "chaincloud_echo",
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
            "Call chaincloud_echo with any JSON object. "
            "After the tool call, answer exactly persistent-tool-ok."
        ),
        context=AgentRuntimeContext(
            workspace_id=workspace.id,
            task_id=None,
            run_id=run_id,
            allowed_tools=("chaincloud_echo",),
            metadata={"persistent_session_key": persistent_session.session_id},
        ),
        max_turns=4,
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=_openai_smoke_base_url(),
        model_api=_openai_smoke_model_api(),
        tool_executor=executor,
        session=persistent_session,
        tracing=AgentRunTracing(
            workflow_name="chaincloud.openai_sdk_tool_session_smoke",
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

    assert executor.calls == ["chaincloud_echo"]
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


class RecordingToolExecutor:
    def __init__(self, output: dict[str, object] | None = None) -> None:
        self.output = output or {"ok": True}
        self.calls: list[str] = []

    def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
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


def json_from_request(request: httpx.Request) -> dict[str, object]:
    payload = json.loads(request.content.decode("utf-8"))
    assert isinstance(payload, dict)
    return payload
