"""Validate structured model outputs against the published JSON schema."""

import json
from typing import Any

from agents.agent_output import AgentOutputSchemaBase
from agents.exceptions import ModelBehaviorError
from jsonschema import Draft202012Validator


class StructuredOutputSchema(AgentOutputSchemaBase):
    def __init__(self, schema: dict[str, Any]) -> None:
        Draft202012Validator.check_schema(schema)
        self.schema = schema
        self.validator = Draft202012Validator(schema)

    def is_plain_text(self) -> bool:
        return False

    def name(self) -> str:
        return "opsmesh_output"

    def json_schema(self) -> dict[str, Any]:
        return self.schema

    def is_strict_json_schema(self) -> bool:
        return False

    def validate_json(self, json_str: str) -> Any:
        try:
            data = json.loads(json_str)
            self.validator.validate(data)
            return data
        except Exception as exc:
            raise ModelBehaviorError("Model output did not match the configured schema") from exc
