from __future__ import annotations

from datetime import UTC, datetime
from typing import NoReturn

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRuntimeContext
from backend.app.capabilities.mcp_execution_adapters import (
    McpToolAdapter,
    McpToolAdapterResolver,
)
from backend.app.capabilities.mcp_execution_types import McpExecutionError
from backend.app.capabilities.mcp_stdio_adapters import (
    DockerRuntimeStdioMcpToolAdapter,
    SelfHostedStdioMcpToolAdapter,
)
from backend.app.capabilities.models import McpServer
from backend.app.core.config import Settings
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.models import SecurityEvent
from backend.app.self_hosted.mcp_jobs import SelfHostedMcpJobService


class ContextualMcpAdapterResolver:
    def __init__(
        self,
        session: Session,
        default_adapter: McpToolAdapter | McpToolAdapterResolver,
        context: AgentRuntimeContext,
        settings: Settings | None = None,
        docker_client: DockerRuntimeClient | None = None,
        secret_service: SecretEncryptionService | None = None,
    ) -> None:
        self._session = session
        self._default_adapter = default_adapter
        self._context = context
        self._settings = settings
        self._docker_client = docker_client
        self._secret_service = secret_service

    def resolve(self, server: McpServer) -> McpToolAdapter:
        if server.server_type != "stdio":
            return self._default_adapter_for(server)
        run = self._current_run()
        if run is None:
            self._deny_stdio("stdio_run_context_invalid", "MCP stdio run context is invalid")
        runtime = self._authorized_runtime_for_run(run)
        if run is not None and runtime is not None and runtime.runtime_provider == "self_hosted":
            return SelfHostedStdioMcpToolAdapter(
                service=SelfHostedMcpJobService(self._session),
                runtime=runtime,
                agent_run_id=run.id,
            )
        if runtime is not None and _is_docker_runtime(runtime):
            if self._docker_client is None:
                raise RuntimeError("Docker runtime MCP execution requires a worker-injected client")
            return DockerRuntimeStdioMcpToolAdapter(
                runtime_manager=RuntimeManager(self._session, self._docker_client),
                runtime=runtime,
                secret_service=self._secret_service,
            )
        self._deny_stdio(
            "stdio_runtime_provider_unsupported",
            "Authorized runtime provider does not support MCP stdio execution",
        )

    def _default_adapter_for(self, server: McpServer) -> McpToolAdapter:
        if isinstance(self._default_adapter, McpToolAdapterResolver):
            return self._default_adapter.resolve(server)
        return self._default_adapter

    def _current_run(self) -> AgentRun | None:
        run = self._session.get(AgentRun, self._context.run_id)
        if run is None or run.workspace_id != self._context.workspace_id:
            return None
        return run

    def _authorized_runtime_for_run(self, run: AgentRun) -> WorkspaceRuntime:
        binding = self._context.runtime_binding
        if (
            binding is None
            or binding.workspace_runtime_id is None
            or binding.workspace_runtime_id != run.runtime_id
        ):
            self._deny_stdio(
                "stdio_runtime_not_authorized",
                "MCP stdio execution requires the run's frozen runtime binding",
            )
        runtime = self._session.get(WorkspaceRuntime, binding.workspace_runtime_id)
        if runtime is None or runtime.workspace_id != run.workspace_id:
            self._deny_stdio(
                "stdio_runtime_unavailable",
                "Authorized MCP stdio runtime is unavailable",
            )
        if runtime.status not in {"active", "running"} or runtime.connection_status != "online":
            self._deny_stdio(
                "stdio_runtime_unavailable",
                "Authorized MCP stdio runtime is not active and online",
            )
        if (
            runtime.runtime_space_id != binding.runtime_space_id
            or run.runtime_space_id != binding.runtime_space_id
        ):
            self._deny_stdio(
                "stdio_runtime_space_mismatch",
                "MCP stdio runtime space does not match the frozen binding",
            )
        if binding.network_disabled and runtime.network_policy.get("disabled") is not True:
            self._deny_stdio(
                "stdio_runtime_network_policy_mismatch",
                "MCP stdio runtime does not enforce the frozen network policy",
            )
        return runtime

    def _deny_stdio(self, code: str, message: str) -> NoReturn:
        self._session.add(
            SecurityEvent(
                workspace_id=self._context.workspace_id,
                user_id=self._context.user_id,
                action="agent_runtime.stdio_blocked",
                outcome="blocked",
                severity="high",
                source_ip=None,
                user_agent=None,
                request_id=None,
                path="internal:agent_runtime_gateway",
                method="WORKER",
                reason=code,
                event_metadata={
                    "agent_run_id": str(self._context.run_id),
                    "authorization_snapshot_fingerprint": self._context.metadata.get(
                        "authorization_snapshot_fingerprint"
                    ),
                    "workspace_runtime_id": (
                        str(self._context.runtime_binding.workspace_runtime_id)
                        if self._context.runtime_binding is not None
                        and self._context.runtime_binding.workspace_runtime_id is not None
                        else None
                    ),
                },
                created_at=datetime.now(UTC),
            )
        )
        self._session.flush()
        raise McpExecutionError(message, code=code)


def _is_docker_runtime(runtime: WorkspaceRuntime) -> bool:
    return (
        runtime.runtime_provider in {"cloud_docker", "docker"}
        or runtime.runtime_type == "docker"
        or runtime.docker_container_id is not None
    )
