from dataclasses import dataclass

from sqlalchemy.orm import Session

from opsmesh.runtime.backends.registry import RuntimeBackendRegistry
from opsmesh.runtime.instances.contracts import DockerRuntimeClient
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.shared.config import Settings
from opsmesh.shared.security.secrets import SecretEncryptionService


@dataclass(frozen=True, slots=True)
class WorkerJobHandlerContext:
    session: Session
    runtime_backends: RuntimeBackendRegistry
    queue: RedisQueue | None = None
    settings: Settings | None = None
    runtime_docker_client: DockerRuntimeClient | None = None

    def require_settings(self, *, context: str) -> Settings:
        if self.settings is None:
            raise ValueError(f"Worker settings are required for {context}")
        return self.settings

    def secret_service(self, *, context: str) -> SecretEncryptionService:
        settings = self.require_settings(context=context)
        return SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
            previous_secrets=settings.credential_encryption_previous_secrets,
        )

    def docker_client(self) -> DockerRuntimeClient:
        if self.runtime_docker_client is None:
            raise RuntimeError("Worker runtime Docker client was not composed at startup")
        return self.runtime_docker_client
