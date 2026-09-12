from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.core.common.config import Settings
from backend.app.core.secrets.service import SecretEncryptionService
from backend.app.domains.agents.runtime.contracts import AgentRuntimeExecutor
from backend.app.domains.capabilities.mcp.transport.contracts import (
    McpToolAdapter,
    McpToolAdapterResolver,
)
from backend.app.runtime.environment.contracts import DockerRuntimeClient
from backend.app.runtime.environment.dependencies import get_docker_runtime_client
from backend.app.runtime.workers.redis_queue import RedisQueue


@dataclass(frozen=True, slots=True)
class WorkerJobHandlerContext:
    session: Session
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
        return self.runtime_docker_client or get_docker_runtime_client()
