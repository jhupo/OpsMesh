"""Provider configuration without native SDK objects or secret values."""

from typing import Any, Literal, Protocol

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract


class ModelProfile(OpsMeshContract):
    id: str = Field(min_length=1, max_length=100)
    model: str
    provider: str = "openai"
    api: Literal["responses", "chat_completions"] = "responses"
    base_url: str | None = None
    secret_ref: str = "env:OPENAI_API_KEY"
    settings: dict[str, Any] = Field(default_factory=dict)
    features: frozenset[str] = frozenset({"text", "tools", "structured_output"})
    supported_settings: frozenset[str] | None = None
    unsupported_settings: frozenset[str] = frozenset()
    revision: int = 1


class ModelProfiles(Protocol):
    async def create(self, profile: ModelProfile) -> ModelProfile: ...
    async def get(self, profile_id: str) -> ModelProfile: ...
    async def get_definition(self, profile_id: str) -> ModelProfile:
        """Read a historical definition regardless of its current execution availability."""
        ...

    async def list(self) -> list[ModelProfile]: ...
    async def update(self, profile: ModelProfile, expected_revision: int) -> ModelProfile: ...
    async def require_features(self, profile_id: str, required: set[str]) -> ModelProfile: ...
