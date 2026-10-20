"""Native SDK guardrails: extension verdicts cross registered Activities only."""

from typing import Any

from agents import GuardrailFunctionOutput, InputGuardrail, OutputGuardrail
from temporalio.exceptions import ActivityError

from opsmesh.orchestration.execution.contracts import PreparedRun
from opsmesh.orchestration.execution.hooks import HookOccurrence, workflow_hooks


def native_guardrails(
    prepared: PreparedRun,
) -> tuple[list[InputGuardrail[Any]], list[OutputGuardrail[Any]]]:
    counters = {"input_guardrail": 0, "output_guardrail": 0}

    async def check(stage: str, value: Any) -> GuardrailFunctionOutput:
        counters[stage] += 1
        try:
            result = await workflow_hooks(
                HookOccurrence(
                    run_id=prepared.reference.run_id,
                    occurrence=f"guardrail:{stage}:{counters[stage]}",
                    stage=stage,
                    payload={"input" if stage == "input_guardrail" else "output": value},
                )
            )
            await workflow_hooks(
                HookOccurrence(
                    run_id=prepared.reference.run_id,
                    occurrence=f"guardrail:{stage}:{counters[stage]}",
                    stage="finalize",
                    payload={"status": "checked"},
                    entered=result["entered"],
                )
            )
        except ActivityError:
            # Return a native tripwire, including an unavailable mandatory
            # guard. Raising here can persist the candidate output as an
            # unknown verdict under the official Session semantics.
            return GuardrailFunctionOutput(output_info=None, tripwire_triggered=True)
        return GuardrailFunctionOutput(output_info=None, tripwire_triggered=False)

    async def input_check(context: Any, agent: Any, value: Any) -> GuardrailFunctionOutput:
        return await check("input_guardrail", value)

    async def output_check(context: Any, agent: Any, value: Any) -> GuardrailFunctionOutput:
        return await check("output_guardrail", value)

    stages = {stage for hook in prepared.request.hooks for stage in hook.stages}
    return (
        [InputGuardrail(input_check, name="installed_input_guardrails", run_in_parallel=False)]
        if "input_guardrail" in stages
        else [],
        [OutputGuardrail(output_check, name="installed_output_guardrails")]
        if "output_guardrail" in stages
        else [],
    )
