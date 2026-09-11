from collections.abc import Mapping

from backend.app.agent_runtime.capability_policy import required_runtime_capabilities
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeAdapter,
    AgentRuntimeCapabilities,
)
from backend.app.agent_runtime.errors import AgentRuntimeCapabilityError
from backend.app.model_providers.provider_keys import (
    is_anthropic_provider,
    is_openai_compatible_provider,
    model_provider_key,
)


class ProviderAgentRuntimeRegistry:
    """Selects a provider SDK adapter without leaking vendor types to callers."""

    def __init__(
        self,
        *,
        adapters: Mapping[str, AgentRuntimeAdapter],
    ) -> None:
        self._adapters = dict(adapters)
        missing = {"openai-compatible", "anthropic"} - self._adapters.keys()
        if missing:
            raise ValueError(
                "Agent runtime adapters are missing provider families: "
                + ", ".join(sorted(missing))
            )

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        adapter = self.adapter_for(request.provider)
        required = required_runtime_capabilities(request)
        missing = tuple(
            capability
            for capability in required
            if not adapter.capabilities.supports(capability)
        )
        if missing:
            raise AgentRuntimeCapabilityError(adapter.capabilities, missing)
        return await adapter.run(request)

    def adapter_for(self, provider: str | None) -> AgentRuntimeAdapter:
        if is_openai_compatible_provider(provider):
            return self._adapters["openai-compatible"]
        if is_anthropic_provider(provider):
            return self._adapters["anthropic"]
        raise ValueError(f"Unsupported model provider: {model_provider_key(provider)}")

    def capability_matrix(self) -> dict[str, AgentRuntimeCapabilities]:
        return {
            provider: adapter.capabilities
            for provider, adapter in sorted(self._adapters.items())
        }
