from backend.app.agents.runtime.multi_provider import ProviderAgentRuntimeRegistry
from backend.app.agents.runtime.providers.claude_runner import ClaudeAgentSDKRunner
from backend.app.agents.runtime.providers.openai_agents import OpenAIAgentsRunner


def build_agent_runtime_registry() -> ProviderAgentRuntimeRegistry:
    return ProviderAgentRuntimeRegistry(
        adapters={
            "openai-compatible": OpenAIAgentsRunner(),
            "anthropic": ClaudeAgentSDKRunner(),
        },
    )
