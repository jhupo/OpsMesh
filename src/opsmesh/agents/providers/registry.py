"""Application-owned provider descriptions; callers supply their authorized view."""

from .config import ProviderConfig
from .models import ModelInfo


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderConfig] = {}

    def register(self, config: ProviderConfig, *, replace: bool = False) -> None:
        if config.id in self._providers and not replace:
            raise ValueError(f"Provider already registered: {config.id}")
        self._providers[config.id] = ProviderConfig.model_validate(config.model_dump(mode="json"))

    def get(self, provider_id: str) -> ProviderConfig:
        return self._providers[provider_id].model_copy(deep=True)

    def list(self) -> list[ProviderConfig]:
        return [self.get(identifier) for identifier in sorted(self._providers)]

    def remove(self, provider_id: str) -> None:
        del self._providers[provider_id]

    def select(self, provider_id: str, model_id: str) -> tuple[ProviderConfig, ModelInfo]:
        provider = self.get(provider_id)
        if not provider.enabled:
            raise ValueError(f"Provider is disabled: {provider_id}")
        for model in provider.models:
            if model.id == model_id:
                if not model.enabled:
                    raise ValueError(f"Model is disabled: {model_id}")
                return provider, model
        raise KeyError(f"Model is not configured: {provider_id}/{model_id}")
