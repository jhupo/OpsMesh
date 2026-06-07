from backend.app.agent_runtime.anthropic import AnthropicMessagesRunner
from backend.app.agent_runtime.contracts import AgentRunner
from backend.app.agent_runtime.fake import FakeAgentRunner
from backend.app.agent_runtime.multi_provider import ProviderDispatchingAgentRunner
from backend.app.agent_runtime.openai_agents import OpenAIAgentsRunner
from backend.app.core.config import Settings
from backend.app.core.resilience import CircuitBreakerConfig


def build_agent_runner(settings: Settings) -> AgentRunner:
    match settings.agent_runner_backend:
        case "fake":
            return FakeAgentRunner()
        case "provider_dispatching":
            openai_runner = OpenAIAgentsRunner(
                max_attempts=settings.external_call_max_attempts,
                circuit_config=CircuitBreakerConfig(
                    failure_threshold=settings.external_call_circuit_failure_threshold,
                    reset_after_seconds=settings.external_call_circuit_reset_seconds,
                ),
            )
            anthropic_runner = AnthropicMessagesRunner(
                max_attempts=settings.external_call_max_attempts,
                circuit_config=CircuitBreakerConfig(
                    failure_threshold=settings.external_call_circuit_failure_threshold,
                    reset_after_seconds=settings.external_call_circuit_reset_seconds,
                ),
            )
            return ProviderDispatchingAgentRunner(
                openai_runner=openai_runner,
                anthropic_runner=anthropic_runner,
            )
