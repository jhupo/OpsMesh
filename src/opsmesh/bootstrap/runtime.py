from opsmesh.platform.settings.policy import operational_configuration
from opsmesh.runtime.backends.docker import DockerSdkRuntimeClient
from opsmesh.runtime.instances.contracts import DockerRuntimeClient
from opsmesh.shared.db.session import SessionLocal


def _control_timeout() -> int:
    with SessionLocal() as session:
        return operational_configuration(session).docker_control_timeout_seconds


def get_default_docker_runtime_client() -> DockerRuntimeClient:
    return DockerSdkRuntimeClient(control_timeout=_control_timeout)
