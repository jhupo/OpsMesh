"""Adapt JSON Schema or registered Python output types to the native SDK."""

import json
from typing import Any

from agents import AgentOutputSchema, AgentOutputSchemaBase
from agents.exceptions import ModelBehaviorError
from agents.strict_schema import ensure_strict_json_schema
from jsonschema import Draft202012Validator, ValidationError

from opsmesh.agents.callbacks import CallbackRegistry
from opsmesh.agents.config.output import JsonSchemaOutput, OutputConfig


class SchemaOutput(AgentOutputSchemaBase):
    def __init__(self, config: JsonSchemaOutput) -> None:
        self.config = config.model_copy(deep=True)
        schema = self.config.schema_
        self.schema = ensure_strict_json_schema(schema) if config.strict else schema
        self.validator = Draft202012Validator(self.schema)

    def is_plain_text(self) -> bool:
        return False

    def name(self) -> str:
        return self.config.name

    def json_schema(self) -> dict[str, Any]:
        from copy import deepcopy

        return deepcopy(self.schema)

    def is_strict_json_schema(self) -> bool:
        return self.config.strict

    def validate_json(self, json_str: str) -> Any:
        def reject_constant(value: str) -> None:
            raise ValueError(f"Invalid JSON constant: {value}")

        try:
            value = json.loads(json_str, parse_constant=reject_constant)
            self.validator.validate(value)
            return value
        except (ValueError, ValidationError) as error:
            raise ModelBehaviorError("Agent output does not match its JSON Schema") from error


def resolve_output(
    config: OutputConfig | None, callbacks: CallbackRegistry
) -> AgentOutputSchemaBase | None:
    if config is None:
        return None
    if isinstance(config, JsonSchemaOutput):
        return SchemaOutput(config)
    output = callbacks.resolve("output_type", config.reference)
    if isinstance(output, AgentOutputSchemaBase):
        if output.is_strict_json_schema() != config.strict:
            raise ValueError("Registered output schema has a different strict setting")
        return output
    if not isinstance(output, type):
        raise TypeError("output_type factory must return a Python type or AgentOutputSchemaBase")
    return AgentOutputSchema(output, strict_json_schema=config.strict)
