from backend.app.agent_runtime.contracts import (
    AgentRunner,
    AgentRunRequest,
    AgentRunResult,
)
from backend.app.model_providers.provider_keys import (
    is_anthropic_provider,
    is_openai_compatible_provider,
    model_provider_key,
)


class ProviderDispatchingAgentRunner:
    def __init__(
        self,
        *,
        openai_runner: AgentRunner,
        anthropic_runner: AgentRunner,
    ) -> None:
        self._openai_runner = openai_runner
        self._anthropic_runner = anthropic_runner

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        return await self._runner_for(request).run(request)

    def _runner_for(self, request: AgentRunRequest) -> AgentRunner:
        provider = model_provider_key(request.provider)
        if provider == "fake":
            raise ValueError("Fake model provider is not supported at runtime")
        if is_openai_compatible_provider(request.provider):
            return self._openai_runner
        if is_anthropic_provider(request.provider):
            return self._anthropic_runner
        raise ValueError(f"Unsupported model provider: {request.provider}")
