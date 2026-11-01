"""Exercise the installed SDK loop against deterministic model responses."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("agents")
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
    ResponseUsage,
)

from opsmesh.framework.agents import (
    AgentExecutionContext,
    AgentRunCompleted,
    AgentRunInterrupted,
    AgentSpecification,
    AgentToolBinding,
    GuardrailBinding,
    HandoffBinding,
    HookBinding,
    ToolBinding,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.runtime.api import GuardrailDecision, SubmitRunRequest
from opsmesh.modules.runtime.internal.backends.openai_agents.backend import OpenAIAgentsBackend
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from opsmesh.modules.runtime.internal.service import PersistentAgentRuntime


def message(text):
    return ResponseOutputMessage(
        id="msg",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


def call(name, arguments="{}", call_id="call1"):
    return ResponseFunctionToolCall(
        id=call_id, type="function_call", name=name, arguments=arguments, call_id=call_id
    )


class ScriptedModel(Model):
    def __init__(self, outputs, *, response_prefix="resp"):
        self.outputs = list(outputs)
        self.inputs = []
        self.response_prefix = response_prefix

    async def get_response(self, *args, **kwargs):
        self.inputs.append((args, kwargs))
        return ModelResponse(
            output=self.outputs.pop(0),
            usage=Usage(requests=1, input_tokens=3, output_tokens=2, total_tokens=5),
            response_id=f"{self.response_prefix}{len(self.inputs)}",
        )

    async def stream_response(self, *args, **kwargs):
        result = await self.get_response(*args, **kwargs)
        text = next((x.content[0].text for x in result.output if x.type == "message"), "")
        if text:
            yield ResponseTextDeltaEvent(
                type="response.output_text.delta",
                delta=text,
                item_id="msg",
                output_index=0,
                content_index=0,
                sequence_number=1,
                logprobs=[],
            )
        response = Response.model_construct(
            id=result.response_id,
            created_at=0,
            model="fake",
            object="response",
            output=result.output,
            status="completed",
            tools=[],
            tool_choice="auto",
            parallel_tool_calls=False,
            usage=ResponseUsage(
                input_tokens=3,
                output_tokens=2,
                total_tokens=5,
                input_tokens_details={"cached_tokens": 0, "cache_write_tokens": 0},
                output_tokens_details={"reasoning_tokens": 0},
            ),
        )
        yield ResponseCompletedEvent(
            type="response.completed", response=response, sequence_number=2
        )


def context(calls=None, agents=None):
    calls = [] if calls is None else calls

    async def invoke(name, args, call_id):
        calls.append((name, args, call_id))
        return "tool-result"

    async def resolve(ref):
        return agents[ref]

    return AgentExecutionContext(
        "run", "session", "user", invoke, resolve_agent=resolve if agents else None
    )


@pytest.mark.asyncio
async def test_real_sdk_tool_approval_serialization_and_resume():
    model = ScriptedModel([[call("lookup", '{"key":"a"}')], [message("done")]])
    registry = RuntimeExtensionRegistry()
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda spec, ctx: model)
    spec = AgentSpecification(
        id="a",
        name="A",
        tools=(
            ToolBinding(
                name="lookup",
                input_schema={
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                    "additionalProperties": False,
                },
                requires_approval=True,
            ),
        ),
    )
    calls = []
    first = await backend.run(spec, "start", context(calls))
    assert isinstance(first, AgentRunInterrupted)
    assert calls == []
    assert "services" not in json.dumps(first.checkpoint)
    stored = json.loads(json.dumps(first.checkpoint))
    result = await backend.run(
        spec, "ignored", context(calls), checkpoint=stored, decisions={first.pending[0].id: True}
    )
    assert isinstance(result, AgentRunCompleted)
    assert result.output == "done"
    assert calls == [("lookup", {"key": "a"}, "call1")]
    assert result.usage.total_tokens == 10


@pytest.mark.asyncio
async def test_dynamic_instructions_hooks_and_schema():
    registry = RuntimeExtensionRegistry()
    observed = []
    registry.register_instruction("personal@1", lambda ctx, spec: f"Hello {ctx.principal_id}")
    registry.register_hook("observe@1", lambda ctx, stage, data: observed.append(stage))
    registry.register_guardrail("safe@1", lambda ctx, data: GuardrailDecision(allowed=True))
    model = ScriptedModel([[message('{"value":3}')]])
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda spec, ctx: model)
    spec = AgentSpecification(
        id="a",
        name="A",
        instruction_ref="personal@1",
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
        hooks=(HookBinding(ref="observe@1"), HookBinding(ref="observe@1", scope="agent")),
        guardrails=(GuardrailBinding(ref="safe@1", stage="input"),),
    )
    output = await backend.run(spec, "start", context())
    assert output.output == {"value": 3}
    assert "agent_start" in observed and "model_end" in observed
    assert model.inputs[0][1]["system_instructions"] == "Hello user"
    first = await backend._builder(context()).build(spec)
    second = await backend._builder(context()).build(spec)
    assert first is not second and first.tools is not second.tools


@pytest.mark.asyncio
async def test_guardrail_blocks_before_model_call():
    registry = RuntimeExtensionRegistry()
    registry.register_guardrail("deny", lambda ctx, data: GuardrailDecision(allowed=False))
    model = ScriptedModel([])
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda spec, ctx: model)
    spec = AgentSpecification(
        id="a", name="A", guardrails=(GuardrailBinding(ref="deny", stage="input"),)
    )
    with pytest.raises(OpsMeshError) as error:
        await backend.run(spec, "bad", context())
    assert error.value.code == "GUARDRAIL_BLOCKED"
    assert model.inputs == []


@pytest.mark.asyncio
async def test_streaming_yields_neutral_delta_and_final_result():
    model = ScriptedModel([[message("streamed")]])
    backend = OpenAIAgentsBackend(
        RuntimeExtensionRegistry(), model_resolver=lambda spec, ctx: model
    )
    events = []

    async def emit(event):
        events.append(event)

    result = await backend.run(AgentSpecification(id="a", name="A"), "hello", context(), emit=emit)
    assert result.output == "streamed"
    assert any(e.kind == "text_delta" and e.payload["text"] == "streamed" for e in events)


@pytest.mark.asyncio
async def test_handoff_uses_same_sdk_loop():
    child = AgentSpecification(id="child", name="Specialist")
    root = AgentSpecification(
        id="root",
        name="Root",
        handoffs=(HandoffBinding(agent_ref=child.ref, tool_name="transfer_specialist"),),
    )
    models = {
        "root": ScriptedModel([[call("transfer_specialist")]]),
        "child": ScriptedModel([[message("specialist answer")]]),
    }
    backend = OpenAIAgentsBackend(
        RuntimeExtensionRegistry(), model_resolver=lambda spec, ctx: models[spec.id]
    )
    result = await backend.run(root, "help", context(agents={child.ref: child}))
    assert result.output == "specialist answer"
    assert result.usage.requests == 2


@pytest.mark.asyncio
async def test_agent_tool_returns_control_to_parent():
    child = AgentSpecification(id="child", name="Specialist")
    root = AgentSpecification(
        id="root",
        name="Root",
        agent_tools=(
            AgentToolBinding(agent_ref=child.ref, name="specialist", description="A specialist"),
        ),
    )
    models = {
        "root": ScriptedModel(
            [[call("specialist", '{"input":"question"}')], [message("parent answer")]]
        ),
        "child": ScriptedModel([[message("child answer")]], response_prefix="child-response"),
    }
    backend = OpenAIAgentsBackend(
        RuntimeExtensionRegistry(), model_resolver=lambda spec, ctx: models[spec.id]
    )
    observations = []
    ctx = context(agents={child.ref: child})
    ctx.services.observe_usage = AsyncMock(wraps=observations.append)
    result = await backend.run(root, "help", ctx)
    assert result.output == "parent answer"
    assert result.usage.requests == 3
    assert len(observations) == 3  # Parent aggregate is not a fourth billable observation.


@pytest.mark.asyncio
async def test_durable_runtime_resumes_real_sdk_approval_with_session_history(tmp_path):
    model = ScriptedModel([[call("lookup")], [message("done")]])
    extensions = RuntimeExtensionRegistry()
    extensions.register_backend(
        "openai_agents",
        lambda: OpenAIAgentsBackend(extensions, model_resolver=lambda spec, ctx: model),
    )
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'sdk.db'}")
    calls = []

    async def factory(view):
        ctx = context(calls)
        ctx.run_id, ctx.session_id, ctx.principal_id = view.id, view.session_id, view.principal_id
        return ctx

    runtime = PersistentAgentRuntime(database, extensions, factory)
    await database.initialize()
    try:
        spec = AgentSpecification(
            id="a", name="A", tools=(ToolBinding(name="lookup", requires_approval=True),)
        )
        run = await runtime.submit(
            SubmitRunRequest(
                principal_id="user",
                session_id="session",
                agent=spec,
                input="hello",
                idempotency_key="sdk",
            )
        )
        waiting = await runtime.execute(run.id)
        assert waiting.status == "waiting_approval", waiting.error
        approved = await runtime.decide(run.id, waiting.approvals[0].id, True, waiting.revision)
        await runtime.resume(run.id, approved.revision)
        other_worker = PersistentAgentRuntime(database, extensions, factory)
        completed = await other_worker.execute(run.id)
        assert completed.status == "completed", completed.error
        assert completed.output == "done"
        assert len(calls) == 1
        history = await runtime.histories.get("session")
        assert history.data["items"][-1]["role"] == "assistant"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_successful_request_usage_survives_later_turn_limit_error():
    observations = []
    model = ScriptedModel([[call("lookup")]])
    backend = OpenAIAgentsBackend(
        RuntimeExtensionRegistry(), model_resolver=lambda spec, ctx: model
    )
    ctx = context()
    ctx.services.observe_usage = AsyncMock(wraps=observations.append)
    spec = AgentSpecification(id="a", name="A", max_turns=1, tools=(ToolBinding(name="lookup"),))
    with pytest.raises(OpsMeshError) as error:
        await backend.run(spec, "hi", ctx)
    assert error.value.code == "RUN_LIMIT_EXCEEDED"
    assert observations[0]["total_tokens"] == 5
    assert error.value.details["usage"]["total_tokens"] == 5


@pytest.mark.asyncio
async def test_resource_compiler_tools_use_authorized_invoker():
    ctx = context(calls := [])

    async def prepare(spec):
        return SimpleNamespace(
            instructions="A prepared skill",
            tools=(ToolBinding(name="search_knowledge", tool_ref="knowledge.search@3"),),
        )

    ctx.services.prepare_agent_resources = prepare
    model = ScriptedModel([[call("search_knowledge")], [message("answer")]])
    backend = OpenAIAgentsBackend(
        RuntimeExtensionRegistry(), model_resolver=lambda spec, ctx: model
    )
    result = await backend.run(AgentSpecification(id="a", name="A"), "find", ctx)
    assert result.output == "answer"
    assert calls[0][0] == "knowledge.search@3"
    assert "A prepared skill" in model.inputs[0][1]["system_instructions"]


@pytest.mark.asyncio
async def test_dynamic_prompt_and_tool_visibility_callbacks():
    registry = RuntimeExtensionRegistry()
    registry.register_prompt(
        "personal-prompt",
        lambda ctx, spec: {"id": "pmpt_test", "variables": {"user": ctx.principal_id}},
    )
    registry.register_visibility("hidden", lambda ctx, target: False)
    model = ScriptedModel([[message("answer")]])
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda spec, ctx: model)
    spec = AgentSpecification(
        id="a",
        name="A",
        prompt_ref="personal-prompt",
        tools=(ToolBinding(name="hidden_tool", enabled_ref="hidden"),),
    )
    await backend.run(spec, "hi", context())
    request = model.inputs[0][1]
    assert request["prompt"]["id"] == "pmpt_test"
    assert request["prompt"]["variables"]["user"] == "user"
    assert request["tools"] == []


@pytest.mark.asyncio
async def test_handoff_filter_controls_model_input_without_dropping_audit_history():
    from opsmesh.modules.runtime.api import HandoffHistorySelection

    registry = RuntimeExtensionRegistry()
    observed = []

    def filter_history(ctx, history):
        observed.append(history)
        return HandoffHistorySelection(
            input_history="filtered handoff", prior_indices=(), new_indices=()
        )

    registry.register_handoff_filter("redact", filter_history)
    child = AgentSpecification(id="child", name="Child")
    root = AgentSpecification(
        id="root",
        name="Root",
        handoffs=(
            HandoffBinding(agent_ref=child.ref, tool_name="transfer", input_filter_ref="redact"),
        ),
    )
    models = {
        "root": ScriptedModel([[call("transfer")]]),
        "child": ScriptedModel([[message("safe")]]),
    }
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda spec, ctx: models[spec.id])
    result = await backend.run(root, "private initial text", context(agents={child.ref: child}))
    incoming = json.dumps(models["child"].inputs[0][1]["input"])
    assert "filtered handoff" in incoming and "private initial text" not in incoming
    assert observed[0].input_history == "private initial text"
    assert any(item.get("type") == "function_call" for item in result.history)


@pytest.mark.asyncio
async def test_agent_tool_custom_output_extractor_changes_parent_visible_result():
    registry = RuntimeExtensionRegistry()
    registry.register_output_extractor("summary", lambda ctx, result: "safe summary")
    child = AgentSpecification(id="child", name="Child")
    root = AgentSpecification(
        id="root",
        name="Root",
        agent_tools=(
            AgentToolBinding(agent_ref=child.ref, name="delegate", output_extractor_ref="summary"),
        ),
    )
    models = {
        "root": ScriptedModel([[call("delegate", '{"input":"work"}')], [message("done")]]),
        "child": ScriptedModel([[message("sensitive child output")]]),
    }
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda spec, ctx: models[spec.id])
    await backend.run(root, "hi", context(agents={child.ref: child}))
    incoming = json.dumps(models["root"].inputs[1][1]["input"])
    assert "safe summary" in incoming and "sensitive child output" not in incoming


@pytest.mark.asyncio
async def test_model_admission_is_rechecked_before_each_request():
    model = ScriptedModel([[call("lookup")], [message("must not run")]])
    context_data = context()
    attempts = []

    async def authorize(profile):
        attempts.append(profile)
        if len(attempts) > 1:
            raise OpsMeshError("BUDGET_EXHAUSTED", "Budget exhausted after the first request")

    context_data.services.authorize_model_request = authorize
    observations = []
    context_data.services.observe_usage = AsyncMock(wraps=observations.append)
    backend = OpenAIAgentsBackend(
        RuntimeExtensionRegistry(), model_resolver=lambda spec, ctx: model
    )
    spec = AgentSpecification(
        id="a", name="A", model_profile_ref="profile@1", tools=(ToolBinding(name="lookup"),)
    )
    with pytest.raises(OpsMeshError) as error:
        await backend.run(spec, "hi", context_data)
    assert error.value.code == "BUDGET_EXHAUSTED"
    assert attempts == ["profile@1", "profile@1"]
    assert len(model.inputs) == 1 and observations[0]["model_profile_ref"] == "profile@1"


@pytest.mark.asyncio
async def test_unknown_provider_outcome_is_observed_without_replaying():
    class LostResponseModel(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            self.inputs.append((args, kwargs))
            raise ConnectionError("response lost after provider may have accepted request")

    model = LostResponseModel([])
    context_data = context()
    observations = []
    context_data.services.observe_usage = AsyncMock(wraps=observations.append)
    backend = OpenAIAgentsBackend(
        RuntimeExtensionRegistry(), model_resolver=lambda spec, ctx: model
    )
    with pytest.raises(OpsMeshError):
        await backend.run(AgentSpecification(id="a", name="A"), "hi", context_data)
    assert len(model.inputs) == 1
    assert len(observations) == 1 and observations[0]["usage_available"] is False
    assert context_data.state.usage_snapshot["usage_available"] is False
