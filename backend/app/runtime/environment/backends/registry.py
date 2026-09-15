from backend.app.runtime.environment.backends.contracts import (
    RuntimeBackend,
    RuntimeBackendCapabilities,
)
from backend.app.runtime.environment.backends.docker import DockerRuntimeBackend
from backend.app.runtime.environment.backends.self_hosted import SelfHostedRuntimeBackend
from backend.app.runtime.environment.contracts import DockerRuntimeClient


class RuntimeBackendRegistry:
    def __init__(self, backends: dict[str, RuntimeBackend]) -> None:
        self._backends = dict(backends)

    def resolve(self, provider: str) -> RuntimeBackend | None:
        return self._backends.get(provider)

    def capabilities(self) -> dict[str, RuntimeBackendCapabilities]:
        return {key: backend.capabilities for key, backend in self._backends.items()}


def build_runtime_backend_registry(
    client: DockerRuntimeClient | None,
) -> RuntimeBackendRegistry:
    docker = DockerRuntimeBackend(client)
    return RuntimeBackendRegistry(
        {
            "docker": docker,
            "cloud_docker": docker,
            "self_hosted": SelfHostedRuntimeBackend(),
        }
    )
