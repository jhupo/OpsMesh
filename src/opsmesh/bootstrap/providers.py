from opsmesh.agents.execution.providers.claude.runner import ClaudeAgentSDKRunner
from opsmesh.agents.execution.providers.openai.runner import OpenAIAgentsRunner
from opsmesh.agents.execution.registry import ProviderAgentRuntimeRegistry


def build_agent_runtime_registry() -> ProviderAgentRuntimeRegistry:
    return ProviderAgentRuntimeRegistry(
        adapters={
            "openai-compatible": OpenAIAgentsRunner(),
            "anthropic": ClaudeAgentSDKRunner(),
        },
    )
