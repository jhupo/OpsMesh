"""Missing provider IDs keep the admitted request identity and separate resource charges."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from agents.items import ModelResponse
from agents.usage import Usage

from opsmesh.framework.agents import AgentSpecification, AgentToolBinding, HookBinding, ToolBinding
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.internal.backends.openai_agents.backend import OpenAIAgentsBackend
from opsmesh.modules.runtime.internal.backends.openai_agents.usage import (
    begin_request,
    publish_request_usage,
    publish_unknown_usage,
)
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from tests.runtime.test_openai_backend import ScriptedModel, call, context, message


async def test_missing_response_id_and_hosted_call_metering() -> None:
    observations: list[dict[str, Any]] = []
    resources: list[dict[str, Any]] = []
    context = SimpleNamespace(
        run_id="run",
        services={"observe_usage": observations.append, "observe_resource_usage": resources.append},
    )
    agent = SimpleNamespace(name="agent", model="test-model")
    begin_request(context, agent)
    identity = context.services["pending_model_requests"][0]["call_id"]
    response = SimpleNamespace(
        response_id=None,
        usage=Usage(requests=1, input_tokens=3, output_tokens=4, total_tokens=7),
        output=[SimpleNamespace(type="web_search_call", id="search-1")],
    )
    await publish_request_usage(context, agent, response)
    assert observations[0]["call_id"] == identity
    assert observations[0]["provider_usage"]["input_tokens"] == 3
    assert resources[0]["call_id"] == "hosted:search-1"
    assert resources[0]["quantity"] == "1"
    assert context.services["pending_model_requests"] == []


@pytest.mark.parametrize("streaming", [False, True])
async def test_runner_success_then_failure_only_marks_the_unfinished_request_unknown(streaming):
    class LostSecondResponse(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            if self.inputs:
                raise ConnectionError("second provider response lost")
            return await super().get_response(*args, **kwargs)

    execution = context()
    observations, durations, hook_tasks = [], [], []
    execution.services["observe_usage"] = observations.append
    execution.services["observe_metric"] = lambda name, value: durations.append((name, value))
    registry = RuntimeExtensionRegistry()

    def trace_request(context, stage, payload):
        if stage == "model_start":
            # Keeping completed hook tasks is a valid tracing extension and ensures
            # Python cannot accidentally reuse their object IDs for end-hook tasks.
            hook_tasks.append(asyncio.current_task())

    registry.register_hook("trace", trace_request)
    model = LostSecondResponse([[call("lookup")]])
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda *_: model)
    spec = AgentSpecification(
        id="a",
        name="A",
        tools=(ToolBinding(name="lookup"),),
        hooks=(HookBinding(ref="trace"),),
    )

    async def emit(event):
        return None

    with pytest.raises(OpsMeshError) as error:
        await backend.run(spec, "same user input", execution, emit=emit if streaming else None)
    assert error.value.code == "PROVIDER_CONNECTION_FAILED"
    assert len(hook_tasks) == 2
    assert len(observations) == 2
    assert [item["usage_available"] for item in observations] == [True, False]
    assert observations[0]["call_id"] == "resp1"
    assert observations[1]["call_id"] != "resp1"
    assert execution.services["usage_snapshot"]["requests"] == 2
    assert execution.services["usage_snapshot"]["input_tokens"] == 3
    assert len(durations) == 2
    assert not execution.services.get("pending_model_requests")


async def test_concurrent_same_agent_tools_pair_each_runner_scope_independently():
    both_started, first_completed = asyncio.Event(), asyncio.Event()
    started = 0

    class ConcurrentChild(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            nonlocal started
            started += 1
            sequence = started
            if sequence == 2:
                both_started.set()
            await both_started.wait()
            if sequence == 2:
                await first_completed.wait()
                raise ConnectionError("one concurrent child response lost")
            return ModelResponse(
                output=[message("child completed")],
                usage=Usage(requests=1, input_tokens=3, output_tokens=2, total_tokens=5),
                response_id="child-response",
            )

    registry = RuntimeExtensionRegistry()
    hook_tasks = []

    def trace_child(context, stage, payload):
        if stage == "model_start":
            hook_tasks.append(asyncio.current_task())
        elif stage == "model_end":
            first_completed.set()

    registry.register_hook("child_trace", trace_child)
    child = AgentSpecification(id="child", name="Child", hooks=(HookBinding(ref="child_trace"),))
    root = AgentSpecification(
        id="root",
        name="Root",
        agent_tools=(AgentToolBinding(agent_ref=child.ref, name="worker"),),
    )
    parent_model = ScriptedModel(
        [[call("worker", '{"input":"same"}', "one"), call("worker", '{"input":"same"}', "two")]]
    )
    child_model = ConcurrentChild([])
    backend = OpenAIAgentsBackend(
        registry,
        model_resolver=lambda spec, _: child_model if spec.id == "child" else parent_model,
    )
    execution = context(agents={child.ref: child})
    observations = []
    execution.services["observe_usage"] = observations.append
    with pytest.raises(OpsMeshError):
        await asyncio.wait_for(backend.run(root, "run both", execution), timeout=5)
    assert started == 2 and len(hook_tasks) == 2
    assert len(observations) == 3
    assert [item["usage_available"] for item in observations] == [True, True, False]
    assert {item["call_id"] for item in observations if item["usage_available"]} == {
        "resp1",
        "child-response",
    }
    assert execution.services["usage_snapshot"]["requests"] == 3
    assert not execution.services.get("pending_model_requests")


async def test_response_id_replay_is_not_rebilled_but_missing_ids_remain_distinct():
    observations = []
    execution = SimpleNamespace(run_id="run", services={"observe_usage": observations.append})
    agent = SimpleNamespace(name="agent", model="model")
    scope = object()
    response = SimpleNamespace(
        response_id="provider-response",
        usage=Usage(requests=1, input_tokens=3, output_tokens=2, total_tokens=5),
        output=[],
    )
    for _ in range(2):
        begin_request(execution, agent, request_scope=scope)
        await publish_request_usage(execution, agent, response, request_scope=scope)
    assert len(observations) == 1 and execution.services["usage_snapshot"]["requests"] == 1
    response.response_id = None
    for _ in range(2):
        begin_request(execution, agent, request_scope=scope)
        await publish_request_usage(execution, agent, response, request_scope=scope)
    assert len(observations) == 3
    assert len({item["call_id"] for item in observations}) == 3
    assert execution.services["usage_snapshot"]["requests"] == 3
    await publish_unknown_usage(execution)
    assert len(observations) == 3
    response.response_id = "provider-response"
    response.usage = Usage(requests=1, input_tokens=99, output_tokens=2, total_tokens=101)
    begin_request(execution, agent, request_scope=scope)
    with pytest.raises(OpsMeshError, match="different usage"):
        await publish_request_usage(execution, agent, response, request_scope=scope)
