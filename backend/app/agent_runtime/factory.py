from backend.app.agent_runtime.claude.runner import ClaudeAgentSDKRunner
from backend.app.agent_runtime.multi_provider import ProviderAgentRuntimeRegistry
from backend.app.agent_runtime.providers.openai_agents import OpenAIAgentsRunner


def build_agent_runtime_registry() -> ProviderAgentRuntimeRegistry:
    return ProviderAgentRuntimeRegistry(
        adapters={
            "openai-compatible": OpenAIAgentsRunner(),
            "anthropic": ClaudeAgentSDKRunner(),
        },
    )
