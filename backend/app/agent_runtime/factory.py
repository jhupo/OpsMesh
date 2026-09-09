from backend.app.agent_runtime.claude_agent import ClaudeAgentSDKRunner
from backend.app.agent_runtime.multi_provider import ProviderAgentRuntimeRegistry
from backend.app.agent_runtime.openai_agents import OpenAIAgentsRunner


def build_agent_runtime_registry() -> ProviderAgentRuntimeRegistry:
    return ProviderAgentRuntimeRegistry(
        adapters={
            "openai-compatible": OpenAIAgentsRunner(),
            "anthropic": ClaudeAgentSDKRunner(),
        },
    )
