from backend.app.agents.execution.registry import ProviderAgentRuntimeRegistry


def get_agent_runtime_registry() -> ProviderAgentRuntimeRegistry:
    raise RuntimeError("Agent runtime registry was not composed at application startup")
