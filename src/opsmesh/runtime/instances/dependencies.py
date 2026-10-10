from opsmesh.runtime.instances.contracts import DockerRuntimeClient


def get_docker_runtime_client() -> DockerRuntimeClient:
    raise RuntimeError("Docker runtime dependency was not composed at application startup")
