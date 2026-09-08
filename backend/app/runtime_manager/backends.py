from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.capabilities.mcp_execution_adapters import McpToolAdapter
from backend.app.capabilities.mcp_stdio_adapters import (
    DockerRuntimeStdioMcpToolAdapter,
    SelfHostedStdioMcpToolAdapter,
)
from backend.app.runtime_manager.contracts import DockerRuntimeClient, RuntimeProjectFilesystem
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtime_manager.project_files import DockerRunProjectFilesystem
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.secrets.service import SecretEncryptionService
from backend.app.self_hosted.mcp_jobs import SelfHostedMcpJobService


@dataclass(frozen=True)
class RuntimeBackendCapabilities:
    mcp_stdio: bool
    asynchronous_jobs: bool
    managed_container_lifecycle: bool
    direct_project_files: bool


class RuntimeBackend(Protocol):
    capabilities: RuntimeBackendCapabilities

    def mcp_adapter(
        self,
        runtime: WorkspaceRuntime,
        run_id: UUID,
        *,
        working_dir: str | None = None,
    ) -> McpToolAdapter: ...

    def project_filesystem(
        self,
        runtime: WorkspaceRuntime,
        run_id: UUID,
    ) -> RuntimeProjectFilesystem | None: ...


class DockerRuntimeBackend:
    capabilities = RuntimeBackendCapabilities(True, False, True, True)

    def __init__(
        self,
        session: Session,
        client: DockerRuntimeClient | None,
        secrets: SecretEncryptionService | None,
    ) -> None:
        self._session = session
        self._client = client
        self._secrets = secrets

    def mcp_adapter(
        self,
        runtime: WorkspaceRuntime,
        run_id: UUID,
        *,
        working_dir: str | None = None,
    ) -> McpToolAdapter:
        if self._client is None:
            raise RuntimeError("Docker runtime MCP execution requires a worker-injected client")
        return DockerRuntimeStdioMcpToolAdapter(
            runtime_manager=RuntimeManager(self._session, self._client),
            runtime=runtime,
            secret_service=self._secrets,
            working_dir=working_dir,
        )

    def project_filesystem(
        self,
        runtime: WorkspaceRuntime,
        run_id: UUID,
    ) -> RuntimeProjectFilesystem | None:
        if self._client is None:
            raise RuntimeError("Docker project files require a worker-injected client")
        return DockerRunProjectFilesystem(self._client, runtime, run_id)


class SelfHostedRuntimeBackend:
    capabilities = RuntimeBackendCapabilities(True, True, False, False)

    def __init__(self, session: Session) -> None:
        self._session = session

    def mcp_adapter(
        self,
        runtime: WorkspaceRuntime,
        run_id: UUID,
        *,
        working_dir: str | None = None,
    ) -> McpToolAdapter:
        return SelfHostedStdioMcpToolAdapter(
            service=SelfHostedMcpJobService(self._session),
            runtime=runtime,
            agent_run_id=run_id,
        )

    def project_filesystem(
        self,
        runtime: WorkspaceRuntime,
        run_id: UUID,
    ) -> RuntimeProjectFilesystem | None:
        return None


class RuntimeBackendRegistry:
    def __init__(self, backends: dict[str, RuntimeBackend]) -> None:
        self._backends = dict(backends)

    def resolve(self, provider: str) -> RuntimeBackend | None:
        return self._backends.get(provider)

    def capabilities(self) -> dict[str, RuntimeBackendCapabilities]:
        return {key: backend.capabilities for key, backend in self._backends.items()}


def build_runtime_backend_registry(
    session: Session,
    client: DockerRuntimeClient | None,
    secrets: SecretEncryptionService | None,
) -> RuntimeBackendRegistry:
    docker = DockerRuntimeBackend(session, client, secrets)
    return RuntimeBackendRegistry(
        {
            "docker": docker,
            "cloud_docker": docker,
            "self_hosted": SelfHostedRuntimeBackend(session),
        }
    )
