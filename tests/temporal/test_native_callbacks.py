"""Native callback metadata and revision constraints at the durable boundary."""

from unittest.mock import AsyncMock

import pytest
from agents import Agent, GuardrailFunctionOutput, InputGuardrail, RunContextWrapper

from opsmesh.agents.callbacks.registry import CallbackRegistry
from opsmesh.agents.config.callbacks import CallbackRef
from opsmesh.execution.preparation import AgentSnapshot
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.workflows.engines.temporal.callbacks import describe_callbacks, register_callbacks


def snapshot(**config):
    return AgentSnapshot.model_validate(
        {"root_id": "root", "definitions": {"root": {"type": "agent", "name": "Root", **config}}}
    )


@pytest.mark.asyncio
async def test_guardrail_proxy_preserves_native_tripwire_and_parallel_metadata():
    reference = CallbackRef(callback_id="guard")
    callbacks = CallbackRegistry()
    callbacks.register(
        "input_guardrail",
        "guard",
        lambda _: InputGuardrail(
            guardrail_function=AsyncMock(), name="access", run_in_parallel=False
        ),
        revision="package:1",
    )
    descriptions = describe_callbacks(callbacks, snapshot(input_guardrails=[reference]))
    workflow_callbacks = CallbackRegistry()
    invoke = AsyncMock(return_value={"output_info": {"allowed": False}, "tripwire_triggered": True})
    register_callbacks(workflow_callbacks, descriptions, invoke)
    guardrail = workflow_callbacks.resolve("input_guardrail", reference)
    result = await guardrail.guardrail_function(
        RunContextWrapper(None), Agent(name="Root"), "input"
    )
    assert isinstance(result, GuardrailFunctionOutput)
    assert result.tripwire_triggered is True
    assert result.output_info == {"allowed": False}
    assert guardrail.get_name() == "access"
    assert guardrail.run_in_parallel is False
    assert invoke.await_args.args[0].revision == "package:1"


def test_unversioned_callback_is_rejected_during_admission():
    registry = CallbackRegistry()
    registry.register("instructions", "dynamic", lambda _: lambda context, agent: "instructions")
    with pytest.raises(InvalidArgumentError, match="immutable revision"):
        describe_callbacks(registry, snapshot(instructions={"callback_id": "dynamic"}))


def test_unsupported_callback_is_rejected_before_workflow_assembly():
    with pytest.raises(InvalidArgumentError, match="tool_use_behavior callback contract"):
        describe_callbacks(
            CallbackRegistry(), snapshot(tool_use_behavior={"callback_id": "custom"})
        )
