from functools import lru_cache

from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.runtime_manager.docker_client import DockerCliRuntimeClient


@lru_cache(maxsize=1)
def get_docker_runtime_client() -> DockerRuntimeClient:
    return DockerCliRuntimeClient()
