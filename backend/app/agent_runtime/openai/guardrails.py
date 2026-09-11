from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from agents import (
    AgentOutputSchemaBase,
    GuardrailFunctionOutput,
    InputGuardrail,
    OutputGuardrail,
)
from agents.exceptions import ModelBehaviorError

from backend.app.agent_runtime.core.contracts import (
    AgentRuntimeGuardrail,
    AgentRuntimeGuardrailResult,
    AgentRuntimeOutputSchema,
)
from backend.app.agent_runtime.core.errors import AgentRuntimeOutputValidationError
from backend.app.agent_runtime.guardrails import (
    evaluate_guardrail,
    validated_structured_output,
)


class OpenAIRuntimeOutputSchemaError(ModelBehaviorError):
    def __init__(self, policy_error: AgentRuntimeOutputValidationError) -> None:
        self.policy_error = policy_error
        super().__init__(policy_error.message)


class OpenAIRuntimeOutputSchema(AgentOutputSchemaBase):
    def __init__(self, definition: AgentRuntimeOutputSchema) -> None:
        self.definition = definition

    def is_plain_text(self) -> bool:
        return False

    def name(self) -> str:
        return self.definition.name

    def json_schema(self) -> dict[str, Any]:
        return dict(self.definition.schema)

    def is_strict_json_schema(self) -> bool:
        return self.definition.strict

    def validate_json(self, json_str: str) -> object:
        try:
            value = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise OpenAIRuntimeOutputSchemaError(
                AgentRuntimeOutputValidationError(
                    schema_name=self.definition.name,
                    schema_version=self.definition.version,
                    validator="json",
                )
            ) from exc
        try:
            return validated_structured_output(self.definition, value).value
        except AgentRuntimeOutputValidationError as exc:
            raise OpenAIRuntimeOutputSchemaError(exc) from exc


def openai_input_guardrails(
    definitions: tuple[AgentRuntimeGuardrail, ...],
    results: list[AgentRuntimeGuardrailResult],
) -> list[InputGuardrail[Any]]:
    guardrails: list[InputGuardrail[Any]] = []
    for definition in definitions:

        async def evaluate(
            context: object,
            agent: object,
            value: object,
            *,
            item: AgentRuntimeGuardrail = definition,
        ) -> GuardrailFunctionOutput:
            del context, agent
            result = evaluate_guardrail(item, value, stage="input")
            results.append(result)
            return GuardrailFunctionOutput(
                output_info=asdict(result),
                tripwire_triggered=result.status == "blocked",
            )

        guardrails.append(
            InputGuardrail(
                guardrail_function=evaluate,
                name=definition.name,
                run_in_parallel=False,
            )
        )
    return guardrails


def openai_output_guardrails(
    definitions: tuple[AgentRuntimeGuardrail, ...],
    results: list[AgentRuntimeGuardrailResult],
) -> list[OutputGuardrail[Any]]:
    guardrails: list[OutputGuardrail[Any]] = []
    for definition in definitions:

        async def evaluate(
            context: object,
            agent: object,
            value: object,
            *,
            item: AgentRuntimeGuardrail = definition,
        ) -> GuardrailFunctionOutput:
            del context, agent
            result = evaluate_guardrail(item, value, stage="output")
            results.append(result)
            return GuardrailFunctionOutput(
                output_info=asdict(result),
                tripwire_triggered=result.status == "blocked",
            )

        guardrails.append(
            OutputGuardrail(
                guardrail_function=evaluate,
                name=definition.name,
            )
        )
    return guardrails


def merged_openai_guardrail_results(
    sdk_result: object,
    collected: list[AgentRuntimeGuardrailResult],
) -> list[AgentRuntimeGuardrailResult]:
    merged: list[AgentRuntimeGuardrailResult] = []
    for attribute in ("input_guardrail_results", "output_guardrail_results"):
        for sdk_guardrail_result in getattr(sdk_result, attribute, ()) or ():
            output = getattr(sdk_guardrail_result, "output", None)
            mapped = _guardrail_result_from_info(getattr(output, "output_info", None))
            if mapped is not None:
                _append_unique(merged, mapped)
    for result in collected:
        _append_unique(merged, result)
    return merged


def _guardrail_result_from_info(value: object) -> AgentRuntimeGuardrailResult | None:
    if not isinstance(value, dict):
        return None
    stage = value.get("stage")
    name = value.get("name")
    kind = value.get("kind")
    status = value.get("status")
    blocking = value.get("blocking")
    evidence = value.get("evidence", {})
    if (
        stage not in {"input", "output"}
        or not isinstance(name, str)
        or not isinstance(kind, str)
        or status not in {"passed", "flagged", "blocked"}
        or not isinstance(blocking, bool)
        or not isinstance(evidence, dict)
    ):
        return None
    return AgentRuntimeGuardrailResult(
        stage=stage,
        name=name,
        kind=kind,
        status=status,
        blocking=blocking,
        evidence=dict(evidence),
    )


def _append_unique(
    results: list[AgentRuntimeGuardrailResult],
    candidate: AgentRuntimeGuardrailResult,
) -> None:
    identity = (candidate.stage, candidate.name, candidate.kind, candidate.status)
    if any(
        (item.stage, item.name, item.kind, item.status) == identity
        for item in results
    ):
        return
    results.append(candidate)
