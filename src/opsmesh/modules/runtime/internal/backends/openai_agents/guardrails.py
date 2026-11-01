"""Translate registered platform guardrails into SDK guardrails."""

import inspect
import json
from typing import Any

from agents import GuardrailFunctionOutput, InputGuardrail, OutputGuardrail
from agents.tool_guardrails import (
    ToolGuardrailFunctionOutput,
    ToolInputGuardrail,
    ToolOutputGuardrail,
)

from opsmesh.framework.agents import AgentExecutionContext, GuardrailBinding
from opsmesh.modules.runtime.api import RuntimeExtensions

from .serialization import serialize_sdk_value


async def evaluate_guardrail(
    extensions: RuntimeExtensions,
    binding: GuardrailBinding,
    context: AgentExecutionContext,
    data: Any,
) -> Any:
    result = extensions.guardrail(binding.ref)(context, data)
    return await result if inspect.isawaitable(result) else result


def build_guardrail(extensions: RuntimeExtensions, binding: GuardrailBinding) -> Any:
    if binding.stage in {"input", "output"}:

        async def check(wrapper: Any, agent: Any, data: Any) -> GuardrailFunctionOutput:
            result = await evaluate_guardrail(
                extensions, binding, wrapper.context, serialize_sdk_value(data)
            )
            return GuardrailFunctionOutput(
                output_info=result.info, tripwire_triggered=not result.allowed
            )

        if binding.stage == "input":
            return InputGuardrail(check, name=binding.ref, run_in_parallel=binding.run_in_parallel)
        return OutputGuardrail(check, name=binding.ref)

    async def check_tool(data: Any) -> ToolGuardrailFunctionOutput:
        payload = (
            serialize_sdk_value(data.output)
            if binding.stage == "tool_output"
            else json.loads(data.context.tool_arguments or "{}")
        )
        result = await evaluate_guardrail(extensions, binding, data.context.context, payload)
        return (
            ToolGuardrailFunctionOutput.allow(result.info)
            if result.allowed
            else ToolGuardrailFunctionOutput.raise_exception(result.info)
        )

    cls = ToolInputGuardrail if binding.stage == "tool_input" else ToolOutputGuardrail
    return cls(check_tool, name=binding.ref)
