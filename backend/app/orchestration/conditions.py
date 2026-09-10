"""Evaluate the small, data-only condition language used by user orchestrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.tasks.models import Task, TaskStep

ConditionState = Literal["true", "false", "pending"]
_MISSING = object()
_TERMINAL_STEP_STATUSES = frozenset({"completed", "failed", "cancelled", "skipped"})
_MAX_CONDITION_DEPTH = 8
_MAX_CONDITION_NODES = 64
_OPERATORS = frozenset(
    {
        "equals",
        "not_equals",
        "contains",
        "not_contains",
        "exists",
        "not_exists",
        "in",
        "not_in",
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
    }
)


class ConditionValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "orchestration_condition_invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ConditionEvaluation:
    state: ConditionState
    reason: str | None = None


def validate_condition(value: object, *, path: str = "condition") -> None:
    """Validate a condition without evaluating user-provided code."""

    _validate(value, path=path, depth=0, counter=[0])


def condition_step_references(value: object) -> set[str]:
    """Collect data dependencies after validating the bounded condition tree."""
    if value in (None, {}):
        return set()
    validate_condition(value)
    assert isinstance(value, dict)
    path = value.get("path")
    if isinstance(path, str) and path.startswith("steps."):
        return {path[len("steps.") :].rsplit(".", 1)[0]}
    references: set[str] = set()
    for key in ("all", "any", "not"):
        children = value.get(key, [])
        for child in children if isinstance(children, list) else [children]:
            references.update(condition_step_references(child))
    return references


def evaluate_task_step_condition(
    session: Session,
    task: Task,
    step: TaskStep,
) -> ConditionEvaluation:
    raw_dependencies = step.dependencies
    if not isinstance(raw_dependencies, dict):
        return ConditionEvaluation("false", "step dependencies are not an object")
    condition = raw_dependencies.get("condition")
    if condition in (None, {}):
        return ConditionEvaluation("true")
    try:
        validate_condition(condition)
    except ConditionValidationError as exc:
        return ConditionEvaluation("false", str(exc))

    steps = session.scalars(
        select(TaskStep).where(
            TaskStep.workspace_id == task.workspace_id,
            TaskStep.task_id == task.id,
        )
    ).all()
    by_package = {
        str(candidate.work_package_id): candidate
        for candidate in steps
        if candidate.work_package_id
    }
    by_id = {str(candidate.id): candidate for candidate in steps}
    return _evaluate(
        condition,
        task=task,
        steps_by_package=by_package,
        steps_by_id=by_id,
        depth=0,
    )


def _validate(
    value: object,
    *,
    path: str,
    depth: int,
    counter: list[int],
) -> None:
    if depth > _MAX_CONDITION_DEPTH:
        raise ConditionValidationError("Condition nesting exceeds 8 levels")
    counter[0] += 1
    if counter[0] > _MAX_CONDITION_NODES:
        raise ConditionValidationError("Condition contains more than 64 nodes")
    if not isinstance(value, dict):
        raise ConditionValidationError(f"{path} must be an object")

    variants = [key for key in ("all", "any", "not", "path") if key in value]
    if set(value) - {"all", "any", "not", "path", "operator", "value"}:
        raise ConditionValidationError(f"{path} contains unsupported fields")
    if len(variants) != 1:
        raise ConditionValidationError(f"{path} must contain exactly one of all, any, not, or path")
    variant = variants[0]
    if variant in {"all", "any"}:
        children = value[variant]
        if not isinstance(children, list) or not children:
            raise ConditionValidationError(f"{path}.{variant} must be a nonempty list")
        if len(children) > 16:
            raise ConditionValidationError(f"{path}.{variant} cannot contain more than 16 items")
        for index, child in enumerate(children):
            _validate(child, path=f"{path}.{variant}[{index}]", depth=depth + 1, counter=counter)
        if "operator" in value or "value" in value:
            raise ConditionValidationError(f"{path} group cannot define operator or value")
        return
    if variant == "not":
        _validate(value[variant], path=f"{path}.not", depth=depth + 1, counter=counter)
        if "operator" in value or "value" in value:
            raise ConditionValidationError(f"{path} group cannot define operator or value")
        return

    raw_path = value.get("path")
    if not isinstance(raw_path, str) or not _valid_path(raw_path):
        raise ConditionValidationError(f"{path}.path is not an allowed state path")
    operator = value.get("operator")
    if not isinstance(operator, str) or operator not in _OPERATORS:
        raise ConditionValidationError(f"{path}.operator is unsupported")
    if operator in {"exists", "not_exists"} and "value" in value and value["value"] is not None:
        raise ConditionValidationError(f"{path} {operator} cannot define value")


def _evaluate(
    value: object,
    *,
    task: Task,
    steps_by_package: dict[str, TaskStep],
    steps_by_id: dict[str, TaskStep],
    depth: int,
) -> ConditionEvaluation:
    if not isinstance(value, dict):
        return ConditionEvaluation("false", "condition is not an object")
    if "all" in value:
        children = value.get("all")
        if not isinstance(children, list):
            return ConditionEvaluation("false", "all is not a list")
        pending = False
        for child in children:
            result = _evaluate(
                child,
                task=task,
                steps_by_package=steps_by_package,
                steps_by_id=steps_by_id,
                depth=depth + 1,
            )
            if result.state == "false":
                return result
            pending = pending or result.state == "pending"
        return ConditionEvaluation("pending" if pending else "true")
    if "any" in value:
        children = value.get("any")
        if not isinstance(children, list):
            return ConditionEvaluation("false", "any is not a list")
        pending = False
        for child in children:
            result = _evaluate(
                child,
                task=task,
                steps_by_package=steps_by_package,
                steps_by_id=steps_by_id,
                depth=depth + 1,
            )
            if result.state == "true":
                return result
            pending = pending or result.state == "pending"
        return ConditionEvaluation("pending" if pending else "false")
    if "not" in value:
        result = _evaluate(
            value.get("not"),
            task=task,
            steps_by_package=steps_by_package,
            steps_by_id=steps_by_id,
            depth=depth + 1,
        )
        if result.state == "pending":
            return result
        return ConditionEvaluation("false" if result.state == "true" else "true")

    path = value.get("path")
    operator = value.get("operator")
    if not isinstance(path, str) or not isinstance(operator, str):
        return ConditionEvaluation("false", "leaf condition is incomplete")
    if path.startswith("steps."):
        referenced_step = _step_for_path(path, steps_by_package, steps_by_id)
        if (
            referenced_step is not None
            and referenced_step.status not in _TERMINAL_STEP_STATUSES
            and operator not in {"exists", "not_exists"}
        ):
            return ConditionEvaluation("pending")
    resolved = _resolve_path(path, task, steps_by_package, steps_by_id)
    if resolved is _MISSING:
        if operator == "exists":
            return ConditionEvaluation("false")
        if operator == "not_exists":
            return ConditionEvaluation("true")
        if path.startswith("steps."):
            referenced_step = _step_for_path(path, steps_by_package, steps_by_id)
            if (
                referenced_step is not None
                and referenced_step.status not in _TERMINAL_STEP_STATUSES
            ):
                return ConditionEvaluation("pending")
        return ConditionEvaluation("false")
    expected = value.get("value")
    return ConditionEvaluation("true" if _compare(resolved, operator, expected) else "false")


def _resolve_path(
    path: str,
    task: Task,
    steps_by_package: dict[str, TaskStep],
    steps_by_id: dict[str, TaskStep],
) -> object:
    parts = path.split(".")
    if parts[0] == "task":
        current: object = {
            "input": task.input,
            "generic_state": task.generic_state,
            "domain_state": task.domain_state,
            "final_output": task.final_output,
        }
        parts = parts[1:]
        for part in parts:
            if not isinstance(current, dict) or part not in current:
                return _MISSING
            current = current[part]
        return current
    step = _step_for_path(path, steps_by_package, steps_by_id)
    if step is None:
        return _MISSING
    field = parts[-1]
    if field == "status":
        return step.status
    if field == "result_summary":
        return step.result_summary if step.result_summary is not None else _MISSING
    return _MISSING


def _step_for_path(
    path: str,
    steps_by_package: dict[str, TaskStep],
    steps_by_id: dict[str, TaskStep],
) -> TaskStep | None:
    parts = path.split(".")
    if len(parts) < 3 or parts[0] != "steps":
        return None
    key = ".".join(parts[1:-1])
    return steps_by_package.get(key) or steps_by_id.get(key)


def _compare(actual: object, operator: str, expected: object) -> bool:
    if operator == "exists":
        return actual is not _MISSING
    if operator == "not_exists":
        return actual is _MISSING
    if operator == "equals":
        return actual == expected
    if operator == "not_equals":
        return actual != expected
    if operator == "contains":
        return _contains(actual, expected)
    if operator == "not_contains":
        return not _contains(actual, expected)
    if operator == "in":
        return isinstance(expected, list) and actual in expected
    if operator == "not_in":
        return isinstance(expected, list) and actual not in expected
    if operator in {"greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal"}:
        if (
            not isinstance(actual, int | float)
            or isinstance(actual, bool)
            or not isinstance(expected, int | float)
            or isinstance(expected, bool)
        ):
            return False
        if operator == "greater_than":
            return actual > expected
        if operator == "greater_than_or_equal":
            return actual >= expected
        if operator == "less_than":
            return actual < expected
        return actual <= expected
    return False


def _contains(actual: object, expected: object) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return expected in actual
    if isinstance(actual, list):
        return expected in actual
    if isinstance(actual, dict) and isinstance(expected, str):
        return expected in actual
    return False


def _valid_path(path: str) -> bool:
    parts = path.split(".")
    if len(parts) < 2:
        return False
    if parts[0] == "task":
        return parts[1] in {"input", "generic_state", "domain_state", "final_output"} and all(
            part.replace("_", "").replace("-", "").isalnum() for part in parts[2:]
        )
    if parts[0] == "steps":
        return (
            len(parts) >= 3
            and parts[-1] in {"status", "result_summary"}
            and all(
                part.replace("_", "").replace("-", "").replace(".", "").isalnum()
                for part in parts[1:-1]
            )
        )
    return False


__all__ = [
    "ConditionEvaluation",
    "ConditionValidationError",
    "evaluate_task_step_condition",
    "validate_condition",
]
