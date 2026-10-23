"""Structured output declarations; executable Python types are registered references."""

from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from .base import ConfigValue, Identifier
from .callbacks import CallbackRef


class JsonSchemaOutput(ConfigValue):
    type: Literal["json_schema"] = "json_schema"
    name: Identifier = "AgentOutput"
    schema_: dict[str, JsonValue] = Field(alias="schema")
    strict: bool = True

    @model_validator(mode="after")
    def validate_schema(self) -> "JsonSchemaOutput":
        from jsonschema import Draft202012Validator, SchemaError

        try:
            Draft202012Validator.check_schema(self.schema_)
        except SchemaError as error:
            raise ValueError("Invalid output JSON Schema") from error

        def check_references(value: JsonValue) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if (
                        key in {"$ref", "$dynamicRef"}
                        and isinstance(item, str)
                        and not item.startswith("#")
                    ):
                        raise ValueError("Output schemas must use local references")
                    check_references(item)
            elif isinstance(value, list):
                for item in value:
                    check_references(item)

        check_references(self.schema_)
        return self


class RegisteredOutput(ConfigValue):
    type: Literal["registered"] = "registered"
    reference: CallbackRef
    strict: bool = True


OutputConfig = Annotated[JsonSchemaOutput | RegisteredOutput, Field(discriminator="type")]
