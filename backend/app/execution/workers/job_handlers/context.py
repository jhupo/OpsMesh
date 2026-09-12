from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.agents.runtime.contracts import AgentRuntimeExecutor
from backend.app.capabilities.mcp.transport.contracts import (
    McpToolAdapter,
    McpToolAdapterResolver,
)
from backend.app.execution.runtime.contracts import DockerRuntimeClient
from backend.app.execution.runtime.dependencies import get_docker_runtime_client
from backend.app.execution.workers.redis_queue import RedisQueue
from backend.app.platform.common.config import Settings
from backend.app.platform.secrets.service import SecretEncryptionService


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
