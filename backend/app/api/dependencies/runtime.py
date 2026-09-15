from backend.app.bootstrap.runtime import get_default_docker_runtime_client
from backend.app.runtime.environment.contracts import DockerRuntimeClient


def get_docker_runtime_client() -> DockerRuntimeClient:
    return get_default_docker_runtime_client()
