from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from opsmesh.agents.providers.policy import canonical_model_provider


class ModelCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    model: str = Field(min_length=1)
    display_name: str
    supports_tools: bool
    supports_vision: bool
    supports_json_mode: bool
    supports_streaming: bool
    context_window_tokens: int | None = Field(default=None, ge=4096)
    notes: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "display_name": self.display_name,
            "capabilities": list(self.capabilities),
            "supports_tools": self.supports_tools,
            "supports_vision": self.supports_vision,
            "supports_json_mode": self.supports_json_mode,
            "supports_streaming": self.supports_streaming,
            "context_window_tokens": self.context_window_tokens,
            "notes": self.notes,
        }

    @property
    def capabilities(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.supports_tools:
            values.append("tools")
        if self.supports_vision:
            values.append("vision")
        if self.supports_json_mode:
            values.append("json_mode")
        if self.supports_streaming:
            values.append("streaming")
        return tuple(values)


def list_model_capabilities(
    catalog: object,
    provider: str | None = None,
    capability: str | None = None,
) -> list[ModelCapability]:
    provider_key = _provider_key(provider)
    capability_key = (capability or "").strip().lower()
    capabilities = TypeAdapter(list[ModelCapability]).validate_python(catalog)
    if provider_key:
        capabilities = [
            item for item in capabilities if _provider_key(item.provider) == provider_key
        ]
    if capability_key:
        capabilities = [item for item in capabilities if capability_key in item.capabilities]
    return capabilities


def resolve_model_capability(
    provider: str | None, model: str | None, catalog: object
) -> ModelCapability | None:
    return next(
        (
            item
            for item in list_model_capabilities(catalog, provider=provider)
            if item.model == model
        ),
        None,
    )


def _provider_key(provider: str | None) -> str:
    if provider is None or not provider.strip():
        return ""
    return canonical_model_provider(provider)


def model_capability_payload(
    provider: str | None, model: str | None, catalog: object
) -> dict[str, object] | None:
    capability = resolve_model_capability(provider, model, catalog)
    return capability.as_dict() if capability is not None else None
