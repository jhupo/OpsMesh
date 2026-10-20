"""Bounded typed parameter substitution; never evaluates user code."""

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from opsmesh_sdk.composition.parameters import normalize
from opsmesh_sdk.composition.parameters import render as render_parameters
from opsmesh_sdk.errors import ValidationError as ParameterError
from referencing.exceptions import Unresolvable

from opsmesh.capabilities.validation import validate_parameter_schema
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.security.redaction import RedactionRules, redact_sensitive_payload


def render(value: Any, parameters: dict[str, Any]) -> Any:
    """Use the single SDK algorithm while preserving the server error contract."""
    try:
        return render_parameters(value, parameters)
    except ParameterError as exc:
        raise InvalidArgumentError(str(exc)) from exc


def validate_parameters(
    schema: dict[str, Any], parameters: dict[str, Any], *, rules: RedactionRules
) -> None:
    try:
        normalize(parameters)
    except ParameterError as exc:
        raise InvalidArgumentError(str(exc)) from exc
    if redact_sensitive_payload(parameters, rules=rules) != parameters:
        raise InvalidArgumentError("Sensitive capability parameters are not supported")
    validate_parameter_schema(schema)
    try:
        Draft202012Validator(schema).validate(parameters)
    except (ValidationError, SchemaError, Unresolvable, RecursionError) as exc:
        raise InvalidArgumentError(
            "Capability parameters do not match the published schema"
        ) from exc
