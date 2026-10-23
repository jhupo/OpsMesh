"""Model selection and validation using the installed SDK's ModelSettings."""

from dataclasses import fields

from agents import ModelSettings
from pydantic import JsonValue, TypeAdapter

from .base import ConfigValue, Identifier


class ModelRef(ConfigValue):
    provider_id: Identifier
    model_id: Identifier


def model_settings(values: dict[str, JsonValue]) -> ModelSettings:
    unknown = values.keys() - {field.name for field in fields(ModelSettings)}
    if unknown:
        raise ValueError(f"Unknown ModelSettings fields: {', '.join(sorted(unknown))}")
    return TypeAdapter(ModelSettings).validate_python(values)
