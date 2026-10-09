from collections.abc import Callable

from backend.app.runtime.backends.docker import DockerRuntimeBackend
from backend.app.runtime.backends.registry import RuntimeBackendRegistry
from backend.app.runtime.backends.self_hosted import SelfHostedRuntimeBackend
from backend.app.runtime.instances.contracts import DockerRuntimeClient


def build_runtime_backend_registry(
    client: DockerRuntimeClient | None,
    transfer_timeout: Callable[[], int],
) -> RuntimeBackendRegistry:
    docker = DockerRuntimeBackend(client, transfer_timeout)
    return RuntimeBackendRegistry(
        {
            "cloud_docker": docker,
            "self_hosted": SelfHostedRuntimeBackend(),
        }
    )
