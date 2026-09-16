from backend.app.runtime.environment.backends.docker import DockerRuntimeBackend
from backend.app.runtime.environment.backends.registry import RuntimeBackendRegistry
from backend.app.runtime.environment.backends.self_hosted import SelfHostedRuntimeBackend
from backend.app.runtime.environment.contracts import DockerRuntimeClient


def build_runtime_backend_registry(
    client: DockerRuntimeClient | None,
) -> RuntimeBackendRegistry:
    docker = DockerRuntimeBackend(client)
    return RuntimeBackendRegistry(
        {
            "cloud_docker": docker,
            "self_hosted": SelfHostedRuntimeBackend(),
        }
    )
