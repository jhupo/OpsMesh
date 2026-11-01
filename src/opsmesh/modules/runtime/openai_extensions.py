"""SDK-free contracts for trusted OpenAI backend provider extension plugins."""

from opsmesh.framework.agents import AgentExecutionContext, SandboxConfiguration
from opsmesh.framework.agents.resources import SandboxProviderBinding


class RemoteDockerSandboxProvider:
    """Deployment-owned endpoint allowlist; native SDK imports happen only on invocation."""

    def __init__(self, *, allowed_endpoints: tuple[str, ...]) -> None:
        self.allowed_endpoints = tuple(allowed_endpoints)

    async def __call__(
        self, configuration: SandboxConfiguration, context: AgentExecutionContext
    ) -> SandboxProviderBinding:
        from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_remote_docker import (
            create_remote_docker_provider,
        )

        return await create_remote_docker_provider(
            configuration, context, allowed_endpoints=self.allowed_endpoints
        )


__all__ = [
    "RemoteDockerSandboxProvider",
]
