"""Bound declarations stay fixed when ordinary MCP parameters are materialized."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from opsmesh.orchestration.configurations.bindings.connections import (
    compile_template,
    validate_connection,
)
from opsmesh.orchestration.configurations.bindings.schemas import BindingConfiguration
from opsmesh.orchestration.configurations.bindings.validation import render, validate_parameters
from opsmesh.orchestration.runtime.models import MCPConnection
from opsmesh.shared.errors import ForbiddenError, InvalidArgumentError
from opsmesh.shared.security.redaction import RedactionRules


def test_stdio_parameters_materialize_without_secret_references() -> None:
    environment_id = uuid4()
    request = compile_template(
        rules=RedactionRules(),
        content={
            "transport": "stdio",
            "command": ["python", "-m", "example"],
            "arguments": ["${directory}"],
            "environment": {"EXAMPLE_DIRECTORY": "${directory}"},
            "parameters_schema": {
                "type": "object",
                "properties": {"directory": {"type": "string"}},
                "required": ["directory"],
            },
        },
        parameters={"directory": "/data"},
        configuration_id=None,
        name="example",
        environment_id=environment_id,
    )
    assert request.environment_id == environment_id
    assert request.command == ["python", "-m", "example", "/data"]
    assert request.environment == {"EXAMPLE_DIRECTORY": "/data"}
    assert "auth_credential_id" not in type(request).model_fields
    with pytest.raises(ValidationError):
        BindingConfiguration(secret_refs={"key": str(uuid4())})


@pytest.mark.parametrize(
    "field", ["transport", "endpoint", "environment_id", "command", "environment"]
)
def test_connection_cannot_change_fixed_materialized_declaration(field: str) -> None:
    expected = compile_template(
        rules=RedactionRules(),
        content={"transport": "stdio", "command": ["python"]},
        parameters={},
        configuration_id=None,
        name="example",
        environment_id=uuid4(),
    )
    connection = MCPConnection(
        transport=expected.transport,
        endpoint=expected.endpoint,
        environment_id=expected.environment_id,
        configuration={"command": expected.command, "environment": expected.environment},
    )
    validate_connection(connection, expected)
    if field in {"command", "environment"}:
        connection.configuration[field] = ["other"] if field == "command" else {"EXAMPLE": "other"}
    else:
        setattr(connection, field, uuid4() if field == "environment_id" else "other")
    with pytest.raises(ForbiddenError):
        validate_connection(connection, expected)


def test_parameters_keep_caller_redaction_rules_and_offline_schema_validation() -> None:
    with pytest.raises(InvalidArgumentError):
        validate_parameters(
            {},
            {"api_key": "sensitive"},
            rules=RedactionRules(exact_keys=frozenset({"api_key"})),
        )
    with pytest.raises(InvalidArgumentError):
        validate_parameters({"$ref": "https://example.com/schema"}, {}, rules=RedactionRules())
    with pytest.raises(InvalidArgumentError):
        validate_parameters({"type": "integer"}, {"value": 1}, rules=RedactionRules())


def test_render_rejects_amplified_output_and_non_string_environment() -> None:
    with pytest.raises(InvalidArgumentError):
        render(["${value}"] * 100, {"value": "x" * 20000})
    with pytest.raises(InvalidArgumentError):
        compile_template(
            rules=RedactionRules(),
            content={
                "transport": "stdio",
                "command": ["python"],
                "environment": {"EXAMPLE": "${value}"},
            },
            parameters={"value": 1},
            configuration_id=None,
            name="example",
            environment_id=uuid4(),
        )
