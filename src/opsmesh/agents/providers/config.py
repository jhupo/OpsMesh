"""Provider configuration contains credential references, never API keys."""

from typing import Literal

from pydantic import Field, HttpUrl, model_validator

from opsmesh.agents.config.base import ConfigValue, Identifier
from opsmesh.shared.network import NetworkConfig

from .models import ModelInfo


class ProviderConfig(ConfigValue):
    id: Identifier
    name: str
    protocol: Literal["openai_responses", "openai_chat_completions"] = "openai_responses"
    base_url: HttpUrl = Field(default=HttpUrl("https://api.openai.com/v1"))
    credential_ref: Identifier = Field(repr=False)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    models: list[ModelInfo] = Field(min_length=1)
    default_model: str | None = None
    enabled: bool = True
    timeout: float = Field(default=60, gt=0, allow_inf_nan=False)
    max_retries: int = Field(default=2, ge=0, le=10)
    request_timeout: float = Field(default=300, gt=0, le=330, allow_inf_nan=False)
    organization: str | None = None
    project: str | None = None

    @model_validator(mode="after")
    def validate_models(self) -> "ProviderConfig":
        names = [model.id for model in self.models]
        if len(names) != len(set(names)):
            raise ValueError("Provider model IDs must be unique")
        if self.default_model is not None and self.default_model not in names:
            raise ValueError("default_model must reference a configured model")
        if (
            self.base_url.username
            or self.base_url.password
            or self.base_url.query
            or self.base_url.fragment
        ):
            raise ValueError("Provider URL must not contain credentials or query parameters")
        return self
