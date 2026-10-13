from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Self

from agents.sandbox.sandboxes.docker import DockerSandboxSessionState
from agents.sandbox.session import BaseSandboxClient, BaseSandboxClientOptions, SandboxSessionState
from temporalio.openai_agents import SandboxClientProvider


@dataclass(frozen=True, slots=True)
class SandboxBackend:
    client: BaseSandboxClient[Any]
    parse_options: Callable[[dict[str, object]], BaseSandboxClientOptions]


def native_resource_identity(state: SandboxSessionState) -> tuple[str, str | None]:
    """Docker resume can replace a missing container while retaining its session UUID."""
    return (
        str(state.session_id),
        state.container_id if isinstance(state, DockerSandboxSessionState) else None,
    )


class SandboxBackendRegistry:
    """Select explicitly installed native providers without a fallback backend."""

    def __init__(
        self,
        backends: dict[str, SandboxBackend],
        *,
        close_resources: Callable[[], None] | None = None,
    ) -> None:
        if not backends or any(not name.strip() for name in backends):
            raise ValueError("At least one named native sandbox backend is required")
        if any(name != backend.client.backend_id for name, backend in backends.items()):
            raise ValueError("Registered backend names must match the SDK backend identity")
        self._backends = dict(backends)
        self._close_resources = close_resources

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        close, self._close_resources = self._close_resources, None
        if close is not None:
            close()

    def require(self, backend_id: str) -> SandboxBackend:
        backend = self._backends.get(backend_id)
        if backend is None:
            raise LookupError("Native sandbox backend is not installed on this worker")
        return backend

    def temporal_providers(self) -> list[SandboxClientProvider]:
        return [
            SandboxClientProvider(name, backend.client) for name, backend in self._backends.items()
        ]
