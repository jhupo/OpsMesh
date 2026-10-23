"""Pure node input/output helpers for the persistent executor, with no I/O."""

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.workflows.conditions import evaluate_condition
from opsmesh.workflows.contracts import ConditionNode, ForeachNode, TransformNode, WorkflowNode
from opsmesh.workflows.mappings import resolve_mapping
from opsmesh.workflows.schema import validate_json, validate_parameter_schema


def validate_value(value: object, schema: dict[str, object]) -> None:
    validate_json({"value": value})
    validate_parameter_schema(schema)
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        raise InvalidArgumentError("A workflow value does not match its declared schema") from exc


def node_arguments(node: WorkflowNode, context: dict[str, object]) -> dict[str, object]:
    result = resolve_mapping(node.arguments, context)
    if not isinstance(result, dict):
        raise InvalidArgumentError("Workflow node arguments must resolve to an object")
    validate_value(result, node.input_schema)
    return result


def should_run(node: WorkflowNode, context: dict[str, object]) -> bool:
    return node.when is None or evaluate_condition(node.when, context)


def local_output(node: WorkflowNode, context: dict[str, object]) -> object:
    """Evaluate only transform/condition/join; effectful nodes need execution."""
    arguments = node_arguments(node, context)
    if isinstance(node, TransformNode):
        output = resolve_mapping(node.value, context)
    elif isinstance(node, ConditionNode):
        output = evaluate_condition(node.condition, context)
    elif node.kind == "join":
        output = arguments
    else:
        raise InvalidArgumentError("This workflow node requires the persistent executor")
    validate_value(output, node.output_schema)
    return output


def foreach_inputs(node: ForeachNode, context: dict[str, object]) -> list[dict[str, object]]:
    items = resolve_mapping(node.items, context)
    if not isinstance(items, list) or len(items) > node.max_items:
        raise InvalidArgumentError("Foreach requires an array within max_items")
    validate_json({"items": items})
    return [{"item": item, "index": index} for index, item in enumerate(items)]
