from collections.abc import Callable

from opsmesh.runtime.backends.docker import DockerRuntimeBackend
from opsmesh.runtime.backends.registry import RuntimeBackendRegistry
from opsmesh.runtime.backends.self_hosted import SelfHostedRuntimeBackend
from opsmesh.runtime.instances.contracts import DockerRuntimeClient


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
