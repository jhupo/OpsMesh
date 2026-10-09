from backend.app.platform.settings.policy import operational_configuration
from backend.app.runtime.backends.docker import DockerSdkRuntimeClient
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.shared.db.session import SessionLocal


def _control_timeout() -> int:
    with SessionLocal() as session:
        return operational_configuration(session).docker_control_timeout_seconds


def get_default_docker_runtime_client() -> DockerRuntimeClient:
    return DockerSdkRuntimeClient(control_timeout=_control_timeout)
