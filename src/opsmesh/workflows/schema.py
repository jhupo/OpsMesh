"""Bounded JSON values and offline JSON Schema validation."""

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.serialization import canonical_json


def validate_json(value: object, *, max_bytes: int = 1_000_000) -> None:
    try:
        size = len(canonical_json({"value": value}).encode())
    except (ValueError, TypeError, RecursionError) as error:
        raise InvalidArgumentError("Workflow values must contain finite JSON data") from error
    if size > max_bytes:
        raise InvalidArgumentError("The workflow value exceeds its size limit")


def validate_parameter_schema(schema: dict[str, object]) -> None:
    validate_json(schema, max_bytes=262144)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise InvalidArgumentError("Invalid workflow JSON Schema") from error

    def check(value: object, depth: int = 0) -> None:
        if depth > 32:
            raise InvalidArgumentError("The workflow Schema exceeds its nesting limit")
        if isinstance(value, dict):
            if any(
                key in value and (not isinstance(value[key], str) or not value[key].startswith("#"))
                for key in ("$ref", "$dynamicRef", "$recursiveRef")
            ):
                raise InvalidArgumentError("Workflow Schemas only allow local references")
            for item in value.values():
                check(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                check(item, depth + 1)

    check(schema)
