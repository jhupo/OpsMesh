from backend.app.agent_runtime.anthropic import AnthropicMessagesRunner
from backend.app.agent_runtime.contracts import AgentRunner
from backend.app.agent_runtime.multi_provider import ProviderDispatchingAgentRunner
from backend.app.agent_runtime.openai_agents import OpenAIAgentsRunner
from backend.app.core.config import Settings
from backend.app.core.resilience import CircuitBreakerConfig


def build_agent_runner(settings: Settings | None = None) -> AgentRunner:
    circuit_config = None
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
    anthropic_runner = AnthropicMessagesRunner(
        max_attempts=max_attempts,
        circuit_config=circuit_config,
    )
    return ProviderDispatchingAgentRunner(
        openai_runner=openai_runner,
        anthropic_runner=anthropic_runner,
    )
