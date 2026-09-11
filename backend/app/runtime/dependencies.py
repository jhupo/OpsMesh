from functools import lru_cache

from backend.app.runtime.contracts import DockerRuntimeClient
from backend.app.runtime.docker_client import DockerSdkRuntimeClient


@lru_cache(maxsize=1)
def get_docker_runtime_client() -> DockerRuntimeClient:
    return DockerSdkRuntimeClient()
