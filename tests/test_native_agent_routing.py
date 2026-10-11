"""Installed SDK graph, Session and resume tests without external model calls."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from agents import Model
from agents.items import ModelResponse
from agents.memory import SQLiteSession
from agents.usage import Usage
from openai import AuthenticationError
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from opsmesh.agents.execution.contracts import (
    AgentRunRequest,
    AgentRuntimeAgentDefinition,
    AgentRuntimeAgentRef,
    AgentRuntimeAgentTarget,
    AgentRuntimeApprovalDecision,
    AgentRuntimeContext,
    AgentRuntimeGuardrail,
    AgentRuntimeGuardrails,
    AgentRuntimeProfile,
    AgentRuntimeStreamEvent,
    AgentRuntimeToolDefinition,
    AgentRuntimeToolResult,
)
from opsmesh.agents.execution.errors import AgentRuntimePolicyError
from opsmesh.agents.execution.providers.openai.runner import OpenAIAgentsRunner
from opsmesh.orchestration.requests.run_gateway import _blocks_text_preview
from opsmesh.orchestration.runs.live_events import RunLivePublisher
from opsmesh.orchestration.runs.result_payloads import run_output_payload
from opsmesh.runtime.agent_host.broker import RuntimeCallbackBroker
from opsmesh.runtime.agent_host.wire import CONTEXT


def message(text):
    return ResponseOutputMessage(
        id=uuid4().hex,
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


def call(name, arguments="{}", call_id=None):
    return ResponseFunctionToolCall(
        id=uuid4().hex,
        type="function_call",
        name=name,
        arguments=arguments,
        call_id=call_id or uuid4().hex,
    )


class ScriptedModel(Model):
    def __init__(self, responses):
        self.responses = list(responses)
        self.inputs = []

    async def get_response(self, **kwargs):
        self.inputs.append(kwargs["input"])
        assert self.responses, "Unexpected model call"
        return ModelResponse(
            output=self.responses.pop(0), usage=Usage(requests=1), response_id=uuid4().hex
        )

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("No streaming model request expected")
        yield


class Tools:
    def __init__(self, approval=False):
        self.approval = approval
        self.authorized = []
        self.executed = []

    async def authorize_agent(self, *, context):
        self.authorized.append(context)

    async def review_tool_call(self, **kwargs):
        return {
            "decision": "require_approval" if self.approval else "allow",
            "risk_level": "medium",
        }

    async def execute_tool(self, **kwargs):
        self.executed.append(kwargs)
        return AgentRuntimeToolResult(status="completed", output={"result": "approved evidence"})

    async def cancel_active_tools(self, **kwargs):
        pass


def request():
    wid = uuid4()
    profile = AgentRuntimeProfile(
        uuid4(), wid, 1, "Router", "router", "Choose a target only", "small"
    )
    return AgentRunRequest(
        profile,
        "Analyze the original question",
        AgentRuntimeContext(wid, None, uuid4()),
        api_key="router-key",
        provider="openai",
        model_api="responses",
        require_handoff=True,
    )


def target(root, name, *, depth=1, max_depth=2, tools=(), handoffs=(), approval=False):
    pid = uuid4()
    context = replace(
        root.context,
        metadata={"agent_profile_id": str(pid)},
        allowed_tools=("evidence",) if approval else (),
        tool_definitions=(
            AgentRuntimeToolDefinition(
                "evidence",
                "product",
                "Read evidence",
                {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                requires_approval=True,
            ),
        )
        if approval
        else (),
    )
    return AgentRuntimeAgentTarget(
        AgentRuntimeAgentDefinition(
            AgentRuntimeAgentRef(name, pid, "expert"),
            context.workspace_id,
            f"Act as {name}",
            model=name,
        ),
        f"transfer_to_{pid.hex}",
        f"Use {name}",
        context,
        8,
        depth,
        max_depth,
        name,
        provider="openai",
        api_key=f"{name}-key",
        model_api="responses",
        nested_tools=tools,
        nested_handoffs=handoffs,
    )


def providers(monkeypatch, models):
    seen = []

    class Provider:
        def __init__(self, **kwargs):
            self.key = kwargs["api_key"]

        def get_model(self, name):
            seen.append((name, self.key))
            return models[name]

    monkeypatch.setattr("opsmesh.agents.execution.providers.openai.runner.OpenAIProvider", Provider)
    return seen


def test_router_handoff_expert_owns_answer_and_sdk_session_history(monkeypatch):
    root = request()
    expert = target(root, "Expert")
    root = replace(root, handoffs=(expert,))
    small = ScriptedModel([[call(expert.tool_name)], [call(expert.tool_name)]])
    strong = ScriptedModel([[message("Detailed expert answer")], [message("Expert follow-up")]])
    seen = providers(monkeypatch, {"small": small, "Expert": strong})

    async def execute():
        session = SQLiteSession(uuid4().hex)
        runner = OpenAIAgentsRunner()
        first = await runner.run(replace(root, session=session))
        second = await runner.run(replace(root, session=session, input_text="Explain the evidence"))
        return first, second, await session.get_items()

    first, second, history = asyncio.run(execute())
    assert first.final_output == "Detailed expert answer"
    assert second.final_agent == expert.target.ref
    assert first.handoffs[0].source.profile_id == root.agent_profile.id
    assert first.handoffs[0].target.profile_id == expert.target.ref.profile_id
    assert run_output_payload(first)["final_agent"]["profile_id"] == str(
        expert.target.ref.profile_id
    )
    assert any(item.get("content") == "Analyze the original question" for item in history)
    assert "Detailed expert answer" in str(small.inputs[-1])
    assert "Analyze the original question" in str(strong.inputs[0])
    assert len(small.inputs) == 2 and len(strong.inputs) == 2
    assert ("Expert", "Expert-key") in seen


def test_router_complex_request_strong_manager_synthesizes_native_expert_tools(monkeypatch):
    root = request()
    expert = replace(target(root, "Expert", depth=2), tool_name="delegate_to_expert")
    manager = target(root, "Manager", tools=(expert,))
    root = replace(root, handoffs=(manager,))
    small = ScriptedModel([[call(manager.tool_name)]])
    strong = ScriptedModel(
        [[call(expert.tool_name, '{"input":"Gather evidence"}')], [message("Strong synthesis")]]
    )
    research = ScriptedModel([[message("Expert evidence")]])
    providers(monkeypatch, {"small": small, "Manager": strong, "Expert": research})
    result = asyncio.run(OpenAIAgentsRunner().run(root))
    assert result.final_output == "Strong synthesis" and result.final_agent == manager.target.ref
    assert len(small.inputs) == 1 and len(strong.inputs) == 2 and len(research.inputs) == 1
    assert "Expert evidence" in str(strong.inputs[-1])
    assert result.agent_tool_calls[0].source == manager.target.ref
    assert [item.agent.name for item in result.model_usages] == [
        "Router",
        "Manager",
        "Expert",
        "Manager",
    ]


@pytest.mark.parametrize("nested", [False, True])
def test_expert_provider_failure_stops_graph_without_root_fallback(monkeypatch, nested):
    root = request()
    expert = target(root, "Expert", depth=2 if nested else 1)
    manager = target(root, "Manager", tools=(expert,)) if nested else None
    root = replace(root, handoffs=(manager or expert,))
    small = ScriptedModel([[call((manager or expert).tool_name)]])
    strong = ScriptedModel([[call(expert.tool_name, '{"input":"Gather evidence"}')]])

    class FailingModel(ScriptedModel):
        async def get_response(self, **kwargs):
            self.inputs.append(kwargs["input"])
            raise AuthenticationError(
                "invalid key",
                response=httpx.Response(401, request=httpx.Request("POST", "https://example.test")),
                body=None,
            )

    research = FailingModel([])
    providers(monkeypatch, {"small": small, "Manager": strong, "Expert": research})
    with pytest.raises(AgentRuntimePolicyError) as raised:
        asyncio.run(OpenAIAgentsRunner().run(root))
    assert raised.value.code == "agent_graph_model_failed"
    assert raised.value.metadata["agent_profile_id"] == str(expert.target.ref.profile_id)
    assert raised.value.metadata["replay_allowed"] is False
    assert [item["agent"]["name"] for item in raised.value.metadata["model_usages"]] == (
        ["Router", "Manager"] if nested else ["Router"]
    )
    assert len(small.inputs) == len(research.inputs) == 1
    assert len(strong.inputs) == (1 if nested else 0)


@pytest.mark.parametrize("nested", [False, True])
def test_handoff_approval_resumes_exact_target_without_rerouting(monkeypatch, nested):
    root = request()
    tools = Tools(approval=True)
    expert = target(root, "Expert", depth=2 if nested else 1, approval=True)
    manager = target(root, "Manager", tools=(expert,)) if nested else None
    root = replace(root, tool_executor=tools, handoffs=(manager or expert,))
    small = ScriptedModel([[call((manager or expert).tool_name)]])
    research = ScriptedModel(
        [[call("evidence", call_id="evidence-call")], [message("Approved expert answer")]]
    )
    strong = ScriptedModel(
        [[call(expert.tool_name, '{"input":"Gather evidence"}')], [message("Approved synthesis")]]
    )
    providers(monkeypatch, {"small": small, "Manager": strong, "Expert": research})

    async def execute():
        runner = OpenAIAgentsRunner()
        paused = await runner.run(root)
        assert paused.resume_state and len(paused.interruptions) == 1
        assert not tools.executed
        interruption = paused.interruptions[0]
        assert interruption.agent == expert.target.ref
        resumed = await runner.run(
            replace(
                root,
                resume_state=paused.resume_state,
                context=replace(
                    root.context, metadata={**root.context.metadata, "trace_id": "resumed-trace"}
                ),
                approval_decisions=(
                    AgentRuntimeApprovalDecision(
                        interruption.tool_call_id, interruption.tool_name, "approved"
                    ),
                ),
            )
        )
        return resumed

    result = asyncio.run(execute())
    assert not result.interruptions
    assert result.final_agent == (manager or expert).target.ref
    assert len(small.inputs) == 1
    assert len(tools.executed) == 1 and tools.executed[0]["context"] == expert.context
    assert tools.executed[0]["tool_call_id"] == "evidence-call"
    assert all(item.agent.name != "Router" for item in result.model_usages)


def test_router_cannot_answer_or_use_foreign_target(monkeypatch):
    root = request()
    expert = target(root, "Expert")
    providers(
        monkeypatch,
        {"small": ScriptedModel([[message("Weak answer")]]), "Expert": ScriptedModel([])},
    )
    with pytest.raises(AgentRuntimePolicyError, match="must hand off"):
        asyncio.run(OpenAIAgentsRunner().run(replace(root, handoffs=(expert,))))
    foreign = replace(expert, target=replace(expert.target, workspace_id=uuid4()))
    with pytest.raises(ValueError, match="another workspace"):
        asyncio.run(OpenAIAgentsRunner().run(replace(root, handoffs=(foreign,))))


def test_nested_expert_output_guardrail_prevents_unvalidated_live_text():
    root = request()
    expert = replace(
        target(root, "Expert", depth=2),
        guardrails=AgentRuntimeGuardrails(
            output=(AgentRuntimeGuardrail("review", "blocked_terms", {"terms": ["secret"]}),)
        ),
    )
    manager = target(root, "Manager", tools=(expert,))
    published = []

    class Bus:
        def publish(self, **kwargs):
            published.append(kwargs)

    publisher = RunLivePublisher(
        Bus(),
        root.context.workspace_id,
        uuid4(),
        root.context.run_id,
        None,
        None,
        allow_text=not _blocks_text_preview(None, (manager,)),
    )
    publisher(AgentRuntimeStreamEvent(1, "output.text.delta", delta="secret"))
    publisher(AgentRuntimeStreamEvent(2, "run.completed", is_terminal=True))
    assert published == [] and publisher.text == ""


def test_runtime_authorizes_frozen_handoff_and_nested_expert_contexts():
    root = request()
    expert = target(root, "Expert", depth=2)
    manager = target(root, "Manager", tools=(expert,))
    tools = Tools()
    broker = RuntimeCallbackBroker(replace(root, handoffs=(manager,), tool_executor=tools))
    asyncio.run(
        broker.dispatch(
            "agent.authorize", {"context": CONTEXT.dump_python(expert.context, mode="json")}
        )
    )
    assert tools.authorized == [expert.context]
    with pytest.raises(ValueError, match="changed its authorized"):
        asyncio.run(
            broker.dispatch(
                "agent.authorize",
                {
                    "context": CONTEXT.dump_python(
                        replace(expert.context, allowed_tools=("escape",)), mode="json"
                    )
                },
            )
        )
