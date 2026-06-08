from dataclasses import dataclass

from backend.app.model_providers.provider_keys import canonical_model_provider


@dataclass(frozen=True)
class ModelCapability:
    provider: str
    model: str
    display_name: str
    supports_tools: bool
    supports_vision: bool
    supports_json_mode: bool
    supports_streaming: bool
    context_window_tokens: int | None = None
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


MODEL_CAPABILITIES: tuple[ModelCapability, ...] = (
    ModelCapability(
        provider="openai",
        model="gpt-5",
        display_name="GPT-5",
        supports_tools=True,
        supports_vision=True,
        supports_json_mode=True,
        supports_streaming=True,
        context_window_tokens=1_000_000,
    ),
    ModelCapability(
        provider="openai",
        model="gpt-5-mini",
        display_name="GPT-5 mini",
        supports_tools=True,
        supports_vision=True,
        supports_json_mode=True,
        supports_streaming=True,
        context_window_tokens=1_000_000,
    ),
    ModelCapability(
        provider="openai",
        model="gpt-4.1",
        display_name="GPT-4.1",
        supports_tools=True,
        supports_vision=True,
        supports_json_mode=True,
        supports_streaming=True,
        context_window_tokens=1_000_000,
    ),
    ModelCapability(
        provider="openai",
        model="gpt-4.1-mini",
        display_name="GPT-4.1 mini",
        supports_tools=True,
        supports_vision=True,
        supports_json_mode=True,
        supports_streaming=True,
        context_window_tokens=1_000_000,
    ),
    ModelCapability(
        provider="openai-compatible",
        model="*",
        display_name="OpenAI-compatible model",
        supports_tools=True,
        supports_vision=False,
        supports_json_mode=True,
        supports_streaming=True,
        context_window_tokens=None,
        notes="Actual support depends on the upstream gateway and selected model.",
    ),
    ModelCapability(
        provider="anthropic",
        model="claude-sonnet-4-6",
        display_name="Claude Sonnet 4.6",
        supports_tools=True,
        supports_vision=True,
        supports_json_mode=False,
        supports_streaming=True,
        context_window_tokens=200_000,
    ),
    ModelCapability(
        provider="anthropic",
        model="claude-sonnet-4-5",
        display_name="Claude Sonnet 4.5",
        supports_tools=True,
        supports_vision=True,
        supports_json_mode=False,
        supports_streaming=True,
        context_window_tokens=200_000,
    ),
    ModelCapability(
        provider="anthropic",
        model="claude-haiku-4-5",
        display_name="Claude Haiku 4.5",
        supports_tools=True,
        supports_vision=True,
        supports_json_mode=False,
        supports_streaming=True,
        context_window_tokens=200_000,
    ),
    ModelCapability(
        provider="anthropic",
        model="*",
        display_name="Claude model",
        supports_tools=True,
        supports_vision=True,
        supports_json_mode=False,
        supports_streaming=True,
        context_window_tokens=200_000,
        notes="Actual support depends on the Anthropic model selected for the agent.",
    ),
)


def list_model_capabilities(
    provider: str | None = None,
    capability: str | None = None,
) -> list[ModelCapability]:
    provider_key = _provider_key(provider)
    capability_key = (capability or "").strip().lower()
    capabilities = list(MODEL_CAPABILITIES)
    if provider_key:
        capabilities = [
            item for item in capabilities if _provider_key(item.provider) == provider_key
        ]
    if capability_key:
        capabilities = [
            item for item in capabilities if capability_key in item.capabilities
        ]
    return capabilities


def resolve_model_capability(provider: str | None, model: str | None) -> ModelCapability | None:
    provider_key = _provider_key(provider)
    model_key = (model or "").strip().lower()
    for capability in MODEL_CAPABILITIES:
        if (
            _provider_key(capability.provider) == provider_key
            and capability.model.lower() == model_key
        ):
            return capability
    for capability in MODEL_CAPABILITIES:
        if _provider_key(capability.provider) == provider_key and capability.model == "*":
            return capability
    return None


def _provider_key(provider: str | None) -> str:
    if provider is None or not provider.strip():
        return ""
    return canonical_model_provider(provider)
