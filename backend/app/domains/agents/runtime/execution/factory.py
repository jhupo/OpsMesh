from backend.app.domains.agents.runtime.claude.runner import ClaudeAgentSDKRunner
from backend.app.domains.agents.runtime.execution.registry import ProviderAgentRuntimeRegistry
from backend.app.domains.agents.runtime.openai.runner import OpenAIAgentsRunner


def build_agent_runtime_registry() -> ProviderAgentRuntimeRegistry:
    return ProviderAgentRuntimeRegistry(
        adapters={
            "openai-compatible": OpenAIAgentsRunner(),
            "anthropic": ClaudeAgentSDKRunner(),
        },
    )
