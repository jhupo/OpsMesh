from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.agents.execution.contracts import AgentRuntimeExecutor
from backend.app.capabilities.mcp.transport.contracts import McpToolAdapter, McpToolAdapterResolver
from backend.app.runtime.backends.registry import RuntimeBackendRegistry
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.runtime.queues.service import RedisQueue
from backend.app.shared.config import Settings
from backend.app.shared.security.secrets import SecretEncryptionService


@dataclass(frozen=True, slots=True)
class WorkerJobHandlerContext:
    session: Session
    runtime_backends: RuntimeBackendRegistry
    queue: RedisQueue | None = None
    agent_runner: AgentRuntimeExecutor | None = None
    settings: Settings | None = None
    mcp_adapter: McpToolAdapter | McpToolAdapterResolver | None = None
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
