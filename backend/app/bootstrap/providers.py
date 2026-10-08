from backend.app.agents.execution.providers.claude.runner import ClaudeAgentSDKRunner
from backend.app.agents.execution.providers.openai.runner import OpenAIAgentsRunner
from backend.app.agents.execution.registry import ProviderAgentRuntimeRegistry


def build_agent_runtime_registry() -> ProviderAgentRuntimeRegistry:
    return ProviderAgentRuntimeRegistry(
        adapters={
            "openai-compatible": OpenAIAgentsRunner(),
            "anthropic": ClaudeAgentSDKRunner(),
        },
    )
