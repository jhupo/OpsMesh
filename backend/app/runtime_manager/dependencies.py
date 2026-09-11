from functools import lru_cache

from backend.app.runtime_manager.core.contracts import DockerRuntimeClient
from backend.app.runtime_manager.docker_client import DockerSdkRuntimeClient


@lru_cache(maxsize=1)
def get_docker_runtime_client() -> DockerRuntimeClient:
    return DockerSdkRuntimeClient()
