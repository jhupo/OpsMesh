"""Finite, typed comparisons for workflow branches."""

from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.workflows.contracts import Condition
from opsmesh.workflows.mappings import MissingReference, resolve_mapping


def condition_values(condition: Condition, *, depth: int = 0) -> list[object]:
    if depth > 16:
        raise InvalidArgumentError("A workflow condition exceeds the nesting limit")
    if condition.operator in {"all", "any", "not"}:
        return [
            value
            for child in condition.conditions
            for value in condition_values(child, depth=depth + 1)
        ]
    return [condition.left, condition.right]


def _equal(left: object, right: object) -> bool:
    # Python considers True == 1; workflow JSON booleans and numbers are distinct.
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(v, right[k]) for k, v in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def evaluate_condition(condition: Condition, context: dict[str, object], *, depth: int = 0) -> bool:
    if depth > 16:
        raise InvalidArgumentError("A workflow condition exceeds the nesting limit")
    operator = condition.operator
    if operator == "all":
        return all(
            evaluate_condition(child, context, depth=depth + 1) for child in condition.conditions
        )
    if operator == "any":
        return any(
            evaluate_condition(child, context, depth=depth + 1) for child in condition.conditions
        )
    if operator == "not":
        return not evaluate_condition(condition.conditions[0], context, depth=depth + 1)
    try:
        left = resolve_mapping(condition.left, context)
    except MissingReference:
        if operator == "exists":
            return False
        raise
    if operator == "exists":
        return True  # Present JSON null is distinct from an absent path.
    right = resolve_mapping(condition.right, context)
    if operator in {"eq", "ne"}:
        equal = _equal(left, right)
        return equal if operator == "eq" else not equal
    if operator in {"contains", "in"}:
        container, item = (left, right) if operator == "contains" else (right, left)
        if isinstance(container, str) and isinstance(item, str):
            return item in container
        if isinstance(container, list):
            return any(_equal(value, item) for value in container)
        if isinstance(container, dict) and isinstance(item, str):
            return item in container
        raise InvalidArgumentError("A membership condition requires compatible JSON operands")
    numeric = type(left) in {int, float} and type(right) in {int, float}
    if not numeric and not (isinstance(left, str) and isinstance(right, str)):
        raise InvalidArgumentError("An ordering condition requires two numbers or two strings")
    # The checks above establish comparable scalar types, without coercing strings/numbers.
    if isinstance(left, str) and isinstance(right, str):
        return {"lt": left < right, "le": left <= right, "gt": left > right, "ge": left >= right}[
            operator
        ]
    if numeric:
        assert isinstance(left, (int, float)) and isinstance(right, (int, float))
        return {"lt": left < right, "le": left <= right, "gt": left > right, "ge": left >= right}[
            operator
        ]
    raise InvalidArgumentError("Invalid ordering operands")
