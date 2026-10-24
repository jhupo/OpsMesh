"""Public model descriptions; these are values, not database models."""

from pydantic import Field, JsonValue, field_validator

from opsmesh.agents.config.base import ConfigValue, Identifier
from opsmesh.agents.config.model import model_settings
from opsmesh.platform.billing.contracts import ModelPricing


class ModelInfo(ConfigValue):
    id: Identifier
    name: str | None = None
    enabled: bool = True
    context_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    supports_tools: bool | None = None
    supports_structured_output: bool | None = None
    supports_images: bool | None = None
    supports_reasoning: bool | None = None
    default_settings: dict[str, JsonValue] = Field(default_factory=dict)
    pricing: ModelPricing | None = None

    @field_validator("default_settings")
    @classmethod
    def validate_settings(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        model_settings(value)
        return value
