from backend.app.agent_runtime.claude_agent import ClaudeAgentSDKRunner
from backend.app.agent_runtime.multi_provider import ProviderAgentRuntimeRegistry
from backend.app.agent_runtime.openai_agents import OpenAIAgentsRunner
from backend.app.core.config import Settings
from backend.app.core.resilience import CircuitBreakerConfig


def build_agent_runtime_registry(
    settings: Settings | None = None,
) -> ProviderAgentRuntimeRegistry:
    circuit_config = CircuitBreakerConfig()
    max_attempts = 2
    if settings is not None:
        max_attempts = settings.external_call_max_attempts
        circuit_config = CircuitBreakerConfig(
            failure_threshold=settings.external_call_circuit_failure_threshold,
            reset_after_seconds=settings.external_call_circuit_reset_seconds,
        )
    openai_runner = OpenAIAgentsRunner(
        max_attempts=max_attempts,
        circuit_config=circuit_config,
    )
    anthropic_runner = ClaudeAgentSDKRunner(
        max_attempts=max_attempts,
        circuit_config=circuit_config,
    )
    return ProviderAgentRuntimeRegistry(
        adapters={
            "openai-compatible": openai_runner,
            "anthropic": anthropic_runner,
        },
    )
