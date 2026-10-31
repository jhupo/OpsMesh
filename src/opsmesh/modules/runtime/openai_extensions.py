"""SDK-free contracts for trusted OpenAI backend provider extension plugins."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from opsmesh.framework.agents import AgentExecutionContext, SandboxConfiguration


@dataclass(frozen=True)
class OpenAISandboxProviderBinding:
    """Opaque native client and options, created only by trusted deployment code.

    validate_manifest authorizes every remote or host mount before creation.
    Without it the bridge permits only inline files/directories/Git entries.
    The factory must validate and enforce requested provider resource options.
    """

    client: Any
    options: Any = None
    validate_manifest: Callable[[dict[str, Any], AgentExecutionContext], Awaitable[None]] | None = (
        None
    )
    aclose: Callable[[], Awaitable[None]] | None = None


class RemoteDockerSandboxProvider:
    """Deployment-owned endpoint allowlist; native SDK imports happen only on invocation."""

    def __init__(self, *, allowed_endpoints: tuple[str, ...]) -> None:
        self.allowed_endpoints = tuple(allowed_endpoints)

    async def __call__(
        self, configuration: SandboxConfiguration, context: AgentExecutionContext
    ) -> OpenAISandboxProviderBinding:
        from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_remote_docker import (
            create_remote_docker_provider,
        )

        return await create_remote_docker_provider(
            configuration, context, allowed_endpoints=self.allowed_endpoints
        )


OpenAISandboxProviderFactory = Callable[
    [SandboxConfiguration, AgentExecutionContext], Awaitable[OpenAISandboxProviderBinding]
]


class SandboxProviderRegistry(Protocol):
    def register_provider(self, reference: str, factory: OpenAISandboxProviderFactory) -> None: ...


__all__ = [
    "OpenAISandboxProviderBinding",
    "OpenAISandboxProviderFactory",
    "RemoteDockerSandboxProvider",
    "SandboxProviderRegistry",
]
