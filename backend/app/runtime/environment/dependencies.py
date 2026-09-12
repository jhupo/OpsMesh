from functools import lru_cache

from backend.app.runtime.environment.backends.docker import DockerSdkRuntimeClient
from backend.app.runtime.environment.contracts import DockerRuntimeClient


@lru_cache(maxsize=1)
def get_docker_runtime_client() -> DockerRuntimeClient:
    return DockerSdkRuntimeClient()
