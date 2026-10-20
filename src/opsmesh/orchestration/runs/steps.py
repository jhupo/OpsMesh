"""Pure bounded mappings and comparisons used by the deterministic Workflow."""

from typing import Any

from opsmesh.orchestration.definitions.contracts import FlowCondition


def map_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            result: Any = context
            for part in value["$ref"].split("."):
                if isinstance(result, dict) and part in result:
                    result = result[part]
                elif isinstance(result, list) and part.isdecimal() and int(part) < len(result):
                    result = result[int(part)]
                else:
                    raise ValueError("The declared flow mapping source is unavailable")
            return result
        return {key: map_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [map_value(item, context) for item in value]
    return value


def compare(condition: FlowCondition, context: dict[str, Any]) -> bool:
    try:
        left, right = map_value(condition.left, context), map_value(condition.right, context)
    except ValueError:
        if condition.operator == "exists":
            return False
        raise
    match condition.operator:
        case "exists":
            return left is not None
        case "eq":
            return type(left) is type(right) and left == right
        case "ne":
            return type(left) is not type(right) or left != right
        case "contains":
            if not isinstance(left, (str, list, dict)):
                raise ValueError("The contains operand is not a collection")
            return right in left
        case _:
            if (
                type(left) is not type(right)
                or isinstance(left, bool)
                or not isinstance(left, (str, int, float))
            ):
                raise ValueError("Comparison operands have incompatible types")
            if condition.operator == "lt":
                return bool(left < right)
            if condition.operator == "le":
                return bool(left <= right)
            if condition.operator == "gt":
                return bool(left > right)
            return bool(left >= right)
