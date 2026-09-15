from backend.app.domains.agents.runtime.providers.claude.runner import ClaudeAgentSDKRunner
from backend.app.domains.agents.runtime.providers.openai.runner import OpenAIAgentsRunner
from backend.app.domains.agents.runtime.registry import ProviderAgentRuntimeRegistry


def build_agent_runtime_registry() -> ProviderAgentRuntimeRegistry:
    return ProviderAgentRuntimeRegistry(
        adapters={
            "openai-compatible": OpenAIAgentsRunner(),
            "anthropic": ClaudeAgentSDKRunner(),
        },
    )
