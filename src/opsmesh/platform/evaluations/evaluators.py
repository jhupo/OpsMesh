"""Deterministic built-ins; external evaluators implement the same adapter contract."""

from pydantic import JsonValue

from opsmesh.platform.evaluations.contracts import DatasetCase, EvaluatorRegistry, Score
from opsmesh.shared.serialization import canonical_json


def exact(case: DatasetCase, output: object, config: dict[str, JsonValue]) -> Score:
    if config:
        raise ValueError("exact does not accept evaluator settings")
    return Score(
        value=float(canonical_json({"value": output}) == canonical_json({"value": case.expected}))
    )


def contains(case: DatasetCase, output: object, config: dict[str, JsonValue]) -> Score:
    if config or not isinstance(case.expected, str):
        raise ValueError("contains requires a string expected value and no settings")
    return Score(value=float(isinstance(output, str) and case.expected in output))


def default_evaluators() -> EvaluatorRegistry:
    registry = EvaluatorRegistry()
    registry.register("exact", "1", exact)
    registry.register("contains", "1", contains)
    return registry
