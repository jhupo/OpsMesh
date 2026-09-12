from backend.app.domains.agents.runtime.multi_provider import ProviderAgentRuntimeRegistry
from backend.app.domains.agents.runtime.providers.claude_runner import ClaudeAgentSDKRunner
from backend.app.domains.agents.runtime.providers.openai_agents import OpenAIAgentsRunner


def build_agent_runtime_registry() -> ProviderAgentRuntimeRegistry:
    return ProviderAgentRuntimeRegistry(
        adapters={
            "openai-compatible": OpenAIAgentsRunner(),
            "anthropic": ClaudeAgentSDKRunner(),
        },
    )
