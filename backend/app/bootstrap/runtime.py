from functools import lru_cache

from backend.app.runtime.backends.docker import DockerSdkRuntimeClient
from backend.app.runtime.instances.contracts import DockerRuntimeClient


@lru_cache(maxsize=1)
def get_default_docker_runtime_client() -> DockerRuntimeClient:
    return DockerSdkRuntimeClient()
