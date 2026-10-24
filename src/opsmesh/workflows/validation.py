"""Workflow authoring validation, separate from authorization and persistence."""

from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.security.redaction import RedactionRules, redact_sensitive_payload
from opsmesh.workflows.contracts import GraphBounds, WorkflowSpec
from opsmesh.workflows.schema import validate_json, validate_parameter_schema


def validate_name(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 120 or "\x00" in value:
        raise InvalidArgumentError("A workflow requires a nonempty name of at most 120 characters")
    return value


def validate_spec(spec: WorkflowSpec, *, redaction: RedactionRules) -> GraphBounds:
    from opsmesh.workflows.graph import validate_graph

    payload = spec.model_dump(mode="json")
    validate_json(payload, max_bytes=262144)
    if redact_sensitive_payload(payload, rules=redaction) != payload:
        raise InvalidArgumentError("Workflow definitions must not contain literal credentials")
    validate_parameter_schema(spec.input_schema)
    validate_parameter_schema(spec.output_schema)
    return validate_graph(spec.graph, spec.limits)
