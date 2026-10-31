"""Real SDK validation, delegated input, completion policies and nested checkpoint behavior."""

from dataclasses import dataclass

import pytest
from pydantic import BaseModel

from opsmesh.framework.agents import (
    AgentSpecification,
    AgentToolBinding,
    GuardrailBinding,
    ToolBinding,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import GuardrailDecision, ToolCompletionDecision
from opsmesh.modules.runtime.internal.backends.openai_agents.backend import OpenAIAgentsBackend
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from tests.runtime.test_openai_backend import ScriptedModel, call, context, message


class Answer(BaseModel):
    answer: str


@dataclass
class DelegatedInput:
    subject: str
    count: int


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output_type,content,expected",
    [
        (Answer, '{"answer":"typed"}', {"answer": "typed"}),
        (list[Answer], '{"response":[{"answer":"typed"}]}', [{"answer": "typed"}]),
        (
            DelegatedInput,
            '{"response":{"subject":"typed","count":2}}',
            {"subject": "typed", "count": 2},
        ),
    ],
)
async def test_registered_python_output_types(output_type, content, expected):
    registry = RuntimeExtensionRegistry()
    registry.register_output_type("result", output_type)
    model = ScriptedModel([[message(content)]])
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda *_: model)
    result = await backend.run(
        AgentSpecification(id="a", name="A", output_type_ref="result"), "hi", context()
    )
    assert result.output == expected


@pytest.mark.asyncio
async def test_structured_agent_input_and_custom_tool_completion():
    registry = RuntimeExtensionRegistry()
    registry.register_output_type("input", DelegatedInput)
    registry.register_agent_input(
        "format",
        lambda ctx, data: f"{data.arguments['count']} facts on {data.arguments['subject']}",
    )
    registry.register_tool_completion(
        "finish",
        lambda ctx, results: ToolCompletionDecision(
            is_final=True, output={"summary": results[0].output}
        ),
    )
    child = AgentSpecification(id="child", name="Child")
    parent = AgentSpecification(
        id="parent",
        name="Parent",
        tool_completion_ref="finish",
        output_schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
        agent_tools=(
            AgentToolBinding(
                name="delegate",
                agent_ref=child.ref,
                input_type_ref="input",
                input_builder_ref="format",
            ),
        ),
    )
    models = {
        "parent": ScriptedModel([[call("delegate", '{"subject":"tests","count":2}')]]),
        "child": ScriptedModel([[message("two facts")]]),
    }
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda spec, _: models[spec.id])
    result = await backend.run(parent, "hi", context(agents={child.ref: child}))
    assert result.output == {"summary": "two facts"}
    assert models["child"].inputs[0][1]["input"] == [
        {"role": "user", "content": "2 facts on tests"}
    ]
    assert len(models["parent"].inputs) == 1


@pytest.mark.asyncio
async def test_custom_completion_survives_approval_checkpoint_and_output_guardrail():
    registry = RuntimeExtensionRegistry()
    registry.register_tool_completion(
        "finish", lambda ctx, results: ToolCompletionDecision(is_final=True, output="blocked")
    )
    registry.register_guardrail(
        "filter", lambda ctx, data: GuardrailDecision(allowed=data != "blocked")
    )
    spec = AgentSpecification(
        id="a",
        name="A",
        tool_completion_ref="finish",
        tools=(ToolBinding(name="write", requires_approval=True),),
        guardrails=(GuardrailBinding(ref="filter", stage="output"),),
    )
    model = ScriptedModel([[call("write")]])
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda *_: model)
    pending = await backend.run(spec, "hi", context())
    assert len(pending.pending) == 1
    with pytest.raises(OpsMeshError) as error:
        await backend.run(
            spec,
            "ignored",
            context(),
            checkpoint=pending.checkpoint,
            decisions={pending.pending[0].id: True},
        )
    assert error.value.code == "GUARDRAIL_BLOCKED"
    assert len(model.inputs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["tool_input", "tool_output", "output"])
async def test_each_guardrail_stage_stops_real_sdk_execution(stage):
    registry = RuntimeExtensionRegistry()
    registry.register_guardrail("deny", lambda ctx, data: GuardrailDecision(allowed=False))
    model = ScriptedModel([[call("lookup")], [message("result")]])
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda *_: model)
    spec = AgentSpecification(
        id="a",
        name="A",
        tools=(ToolBinding(name="lookup"),),
        guardrails=(GuardrailBinding(ref="deny", stage=stage),),
    )
    with pytest.raises(OpsMeshError) as error:
        await backend.run(spec, "hi", context())
    assert error.value.code == "GUARDRAIL_BLOCKED"


@pytest.mark.asyncio
async def test_nested_child_tool_approval_round_trips_sdk_state():
    child = AgentSpecification(
        id="child", name="Child", tools=(ToolBinding(name="write", requires_approval=True),)
    )
    root = AgentSpecification(
        id="root",
        name="Root",
        agent_tools=(AgentToolBinding(name="delegate", agent_ref=child.ref),),
    )
    models = {
        "root": ScriptedModel([[call("delegate", '{"input":"work"}')], [message("parent done")]]),
        "child": ScriptedModel([[call("write", call_id="inner")], [message("child done")]]),
    }
    registry = RuntimeExtensionRegistry()
    backend = OpenAIAgentsBackend(registry, model_resolver=lambda spec, _: models[spec.id])
    pending = await backend.run(root, "hi", context(agents={child.ref: child}))
    assert pending.pending[0].tool_name == "write"
    fresh = OpenAIAgentsBackend(registry, model_resolver=lambda spec, _: models[spec.id])
    result = await fresh.run(
        root,
        "ignored",
        context(agents={child.ref: child}),
        checkpoint=pending.checkpoint,
        decisions={pending.pending[0].id: True},
    )
    assert result.output == "parent done"
    assert len(models["root"].inputs) == 2 and len(models["child"].inputs) == 2


@pytest.mark.asyncio
async def test_partial_approval_keeps_remaining_action_pending():
    spec = AgentSpecification(
        id="a",
        name="A",
        tools=(
            ToolBinding(name="first", requires_approval=True),
            ToolBinding(name="second", requires_approval=True),
        ),
    )
    model = ScriptedModel(
        [[call("first", call_id="first1"), call("second", call_id="second1")], [message("done")]]
    )
    backend = OpenAIAgentsBackend(RuntimeExtensionRegistry(), model_resolver=lambda *_: model)
    ctx = context()
    pending = await backend.run(spec, "hi", ctx)
    decision = next(item for item in pending.pending if item.tool_name == "first")
    remaining = await backend.run(
        spec, "ignored", ctx, checkpoint=pending.checkpoint, decisions={decision.id: True}
    )
    assert len(remaining.pending) == 1 and remaining.pending[0].tool_name == "second"
    result = await backend.run(
        spec,
        "ignored",
        ctx,
        checkpoint=remaining.checkpoint,
        decisions={remaining.pending[0].id: False},
    )
    assert result.output == "done" and len(model.inputs) == 2
