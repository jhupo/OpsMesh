import asyncio
import json
from uuid import uuid4

import pytest
from agents import Agent, RunContextWrapper
from agents.extensions.memory import SQLAlchemySession
from agents.usage import Usage
from sqlalchemy.ext.asyncio import create_async_engine

import opsmesh.agents.execution.providers.openai.executor as openai_runtime
from opsmesh.agents.execution.contracts import (
    AgentExecutionContext,
    AgentRunRequest,
    AgentToolSpec,
    AgentTracingConfig,
    ToolExecutionResult,
)
from opsmesh.agents.execution.providers.openai.executor import OpenAIAgentExecutor
from opsmesh.agents.execution.providers.openai.results import (
    sdk_interruptions,
    sdk_resume_state,
    sdk_safe_raw_output,
)
from opsmesh.agents.execution.providers.openai.tools import (
    build_product_function_tool,
    build_product_function_tools,
    runtime_allowed_tools,
)
from opsmesh.agents.profiles.models import AgentProfile


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
        id=uuid4(),
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Find market trends",
        context=AgentExecutionContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        provider="openai-compatible",
        api_key="sk-test",
        model_provider_credential_id=uuid4(),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(OpenAIAgentExecutor().run(request))

    assert attempts == 1


def test_openai_agents_runner_uses_only_canonical_model_api_values() -> None:
    assert openai_runtime._use_responses_api("response") is None
    assert openai_runtime._use_responses_api("responses") is True
    assert openai_runtime._use_responses_api("chat") is None
    assert openai_runtime._use_responses_api("chat-completions") is None
    assert openai_runtime._use_responses_api("chat_completions") is False
    assert openai_runtime._use_responses_api("future-api") is None


def test_openai_agents_result_mapper_omits_private_history_from_raw_output() -> None:
    from agents.usage import Usage

    class Result:
        new_items = []
        interruptions = []
        final_output = "done"
        context_wrapper = RunContextWrapper(context=None, usage=Usage(requests=1))
        last_agent = Agent(name="Researcher")

        def to_input_list(self, *, mode: str) -> list[dict[str, object]]:
            assert mode == "normalized"
            return [
                {
                    "role": "assistant",
                    "content": "done api_key=sk-resume-secret",
                    "metadata": {"authorization": "Bearer resume-token"},
                }
            ]

    payload = sdk_safe_raw_output(Result())

    assert payload["final_output"] == "done"
    assert payload["last_agent"] == "Researcher"
    assert payload["usage"]["requests"] == 1
    assert set(payload) == {"final_output", "last_agent", "usage"}
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

    state = sdk_resume_state(Result())

    assert state is not None
    assert state.provider == "openai_agents"
    assert state.schema_version == "1.10"
    assert json.loads(state.serialized_state) == {
        "$schemaVersion": "1.10",
        "current_turn": 1,
    }


@pytest.mark.parametrize("stream", [False, True])
def test_openai_agents_runner_preserves_native_history_across_many_turns(
    monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    import httpx
    from agents.models.openai_responses import OpenAIResponsesModel
    from openai import AsyncOpenAI
    from openai.resources.responses import AsyncResponses

    seen: list[list[dict[str, object]]] = []

    async def forbid_compaction(*args: object, **kwargs: object) -> None:
        raise AssertionError("A normal Session must not make a separate compaction request")

    monkeypatch.setattr(AsyncResponses, "compact", forbid_compaction)

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        assert "context_management" not in body
        seen.append(body["input"])
        turn = len(seen) - 1
        response = {
            "id": f"resp-{turn}",
            "created_at": 0,
            "model": "gpt-4.1",
            "object": "response",
            "status": "completed",
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
            "output": [
                {
                    "id": f"message-{turn}",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": f"answer-{turn}", "annotations": []}
                    ],
                }
            ],
        }
        if body.get("stream"):
            event = {"type": "response.completed", "sequence_number": 0, "response": response}
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text="data: " + json.dumps(event) + "\n\n",
            )
        return httpx.Response(200, json=response)

    profile = AgentProfile(
        workspace_id=uuid4(),
        name="History",
        role="researcher",
        instructions="Use the conversation history.",
        model="gpt-4.1",
        model_settings={},
        id=uuid4(),
    )

    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with AsyncOpenAI(
            api_key="sk-test",
            base_url="https://offline.test/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as client:
            model = OpenAIResponsesModel(model="gpt-4.1", openai_client=client)
            from agents import ModelProvider

            from tests.fixtures.sdk import install_model_provider

            class Provider(ModelProvider):
                def get_model(self, name):
                    return model

            install_model_provider(monkeypatch, Provider())
            try:
                # Reopen storage every turn to verify persisted history, beyond the former
                # wrapper's ten-item default trigger, in both SDK execution paths.
                for turn in range(7):
                    session = SQLAlchemySession(
                        "history-regression", engine=engine, create_tables=True
                    )
                    result = await OpenAIAgentExecutor().run(
                        AgentRunRequest(
                            agent_profile=profile,
                            input_text=f"question-{turn}",
                            context=AgentExecutionContext(
                                workspace_id=profile.workspace_id,
                                task_id=None,
                                run_id=uuid4(),
                            ),
                            api_key="sk-test",
                            stream=stream,
                            model_provider_credential_id=uuid4(),
                        ),
                        session=session,
                    )
                    assert result.final_output == f"answer-{turn}"
                    history = await session.get_items()
                    assert len(history) == 2 * (turn + 1)
                    assert history[0] == {"role": "user", "content": "question-0"}
                assert len(seen) == 7
                assert [item["content"] for item in seen[-1] if item.get("role") == "user"] == [
                    f"question-{turn}" for turn in range(7)
                ]
                assert [
                    item["content"][0]["text"]
                    for item in seen[-1]
                    if item.get("role") == "assistant"
                ] == [f"answer-{turn}" for turn in range(6)]
            finally:
                await engine.dispose()

    asyncio.run(scenario())


def test_openai_agents_runner_passes_tracing_run_config_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        context_wrapper = RunContextWrapper(context=None, usage=Usage())
        last_agent = Agent(name="Test")
        new_items = []
        interruptions = []
        final_output = "done"

    captured: dict[str, object] = {}

    async def fake_runner_run(*args: object, **kwargs: object) -> Result:
        captured["kwargs"] = kwargs
        result = Result()
        result.last_agent = args[0]
        return result

    monkeypatch.setattr(openai_runtime.Runner, "run", fake_runner_run)
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Researcher",
        role="researcher",
        instructions="Research carefully.",
        model="gpt-4.1",
        model_settings={},
        id=uuid4(),
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Trace this.",
        context=AgentExecutionContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        tracing=AgentTracingConfig(
            workflow_name="opsmesh.team_agent_run",
            trace_id="trace_123",
            group_id="workspace:team_agent:team-1:agent-1",
            metadata={
                "team": {"team_id": "team-1"},
                "persistent_session_key": "workspace:team_agent:team-1:agent-1",
            },
        ),
        api_key="sk-test",
        model_provider_credential_id=uuid4(),
    )

    result = asyncio.run(OpenAIAgentExecutor().run(request))
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
        id=uuid4(),
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Trace this.",
        context=AgentExecutionContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
        ),
        tracing=AgentTracingConfig(
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
        model_provider_credential_id=uuid4(),
    )

    run_config = OpenAIAgentExecutor()._run_config(request)

    assert run_config is not None
    assert run_config.trace_metadata["run_model"] == "gpt-4.1-mini"
    assert run_config.trace_metadata["model_provider_provider"] == "openai-compatible"
    assert run_config.trace_metadata["model_provider_credential_id"] == str(credential_id)
    assert run_config.trace_metadata["model_provider_model_api"] == "responses"
    serialized = json.dumps(run_config.trace_metadata)
    assert "api_key" not in serialized
    assert "base_url" not in serialized


def test_openai_agents_runner_tools_include_provenance_guardrail() -> None:
    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Designer",
        role="designer",
        instructions="Design carefully.",
        model="gpt-4.1",
        model_settings={},
        id=uuid4(),
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Create a poster",
        context=AgentExecutionContext(
            workspace_id=profile.workspace_id,
            task_id=None,
            run_id=uuid4(),
            allowed_tools=("generate_image",),
            tool_definitions=(_runtime_tool("generate_image"),),
        ),
        tool_executor=RecordingToolExecutor(),
        api_key="sk-test",
        model_provider_credential_id=uuid4(),
    )

    definition = _runtime_tool("generate_image")
    tool = build_product_function_tool(
        definition, request.tool_executor, runtime_context=request.context
    )

    assert tool.description == "Execute generate_image."
    assert tool.params_json_schema == _runtime_tool("generate_image").input_schema
    assert tool.tool_input_guardrails is not None
    assert tool.tool_input_guardrails[0].name == ("generate_image:runtime_allowed_tool_provenance")


def test_openai_tool_bridge_maps_dynamic_sdk_approval_interruption() -> None:
    calls: list[str] = []

    class Executor:
        async def review_tool_call(self, **_: object) -> dict[str, object]:
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
        ) -> ToolExecutionResult:
            calls.append(tool_call_id)
            return ToolExecutionResult(status="completed", output={"ok": True})

    profile = AgentProfile(
        workspace_id=uuid4(),
        name="Approver",
        role="worker",
        instructions="Use tools.",
        model="gpt-4.1",
        id=uuid4(),
    )
    context = AgentExecutionContext(
        workspace_id=profile.workspace_id,
        task_id=None,
        run_id=uuid4(),
        allowed_tools=("write_artifact",),
        tool_definitions=(
            AgentToolSpec(
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
        model_provider_credential_id=uuid4(),
    )
    tool = build_product_function_tools(request)[0]

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
    item.agent = Agent(name="Approver", tools=[tool])
    result = type("Result", (), {"interruptions": [item]})()

    interruptions = sdk_interruptions(result)
    from agents.tool_context import ToolContext

    tool_context = ToolContext(
        context=context,
        tool_name=item.name,
        tool_call_id=item.call_id,
        tool_arguments=item.arguments,
    )
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
        async def review_tool_call(self, **_: object) -> dict[str, object]:
            return {"decision": decision}

        async def execute_tool(self, **_: object) -> ToolExecutionResult:
            raise AssertionError("Denied tool must not execute")

    context = AgentExecutionContext(workspace_id=uuid4(), task_id=None, run_id=uuid4())
    tool = build_product_function_tool(
        AgentToolSpec(
            name="write_artifact",
            source="product",
            description="Write",
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


def _runtime_tool(name: str) -> AgentToolSpec:
    return AgentToolSpec(
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

    async def review_tool_call(self, **_: object) -> dict[str, object]:
        return {"decision": "allow", "risk_level": "low", "reasons": []}

    async def execute_tool(
        self,
        *,
        context: AgentExecutionContext,
        tool_name: str,
        arguments: dict[str, object],
        tool_call_id: str,
        approval_granted: bool,
    ) -> ToolExecutionResult:
        self.calls.append(tool_name)
        return ToolExecutionResult(
            status="completed",
            output=self.output,
            metadata={"executor": "recording"},
        )


def test_openai_graph_uses_credential_reference_without_decrypting():
    from opsmesh.agents.execution.providers.openai.contracts import AgentModelReference

    profile = AgentProfile(
        id=uuid4(),
        workspace_id=uuid4(),
        name="Graph",
        role="expert",
        model="configured",
        instructions="Answer",
        model_settings={},
    )
    request = AgentRunRequest(
        profile,
        "Question",
        AgentExecutionContext(profile.workspace_id, None, uuid4()),
        model="selected",
        model_provider_credential_id=uuid4(),
        model_api="responses",
    )
    agent = OpenAIAgentExecutor()._build_agent(request)
    reference = AgentModelReference.decode(agent.model)
    assert reference.model == "selected"
    assert reference.credential_id == request.model_provider_credential_id
    assert reference.workspace_id == profile.workspace_id
    assert "api_key" not in agent.model
    with pytest.raises(ValueError, match="authorized model references"):
        from dataclasses import replace

        OpenAIAgentExecutor()._build_agent(replace(request, model_provider_credential_id=None))
