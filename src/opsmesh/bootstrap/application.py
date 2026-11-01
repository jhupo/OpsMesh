"""Application lifecycle and shared entry-point use cases."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from opsmesh import dependencies
from opsmesh.application.coordinator import BackgroundCoordinator
from opsmesh.application.use_cases import ApplicationUseCases
from opsmesh.bootstrap.workers import create_run_worker_launcher
from opsmesh.framework import init, load_plugin
from opsmesh.framework.drivers import OpsMeshNetworkDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import DispatchContext, OpsMeshEvent
from opsmesh.framework.message import DispatchResult
from opsmesh.infrastructure.telemetry.tracing import ApplicationTraceExporter
from opsmesh.modules.memory.api import MemoryJob
from opsmesh.modules.runtime.api import (
    AgentRunView,
    RunOverrides,
)
from opsmesh.modules.workspaces.api import WorkspaceDescriptor

from .services import OpsMeshServices
from .settings import OpsMeshSettings


class OpsMeshApplication:
    def __init__(self, settings: OpsMeshSettings | None = None) -> None:
        self.settings = settings or OpsMeshSettings.from_environment()
        self.driver = init(OpsMeshNetworkDriver())
        self.services = OpsMeshServices(self.settings, self.driver, self.dispatch)
        self._use_cases = ApplicationUseCases(self)
        self.services.run_management.submit_run = self.submit_run
        self.services.trigger_bridge.submit_run = self.submit_run
        self.launcher = create_run_worker_launcher(
            self.services.runtime,
            self.settings,
            mode=self.settings.run_worker_mode,
            docker_image=self.settings.run_worker_image,
            cpu=self.settings.run_worker_cpu,
            memory_mb=self.settings.run_worker_memory_mb,
            pids_limit=self.settings.run_worker_pids_limit,
            network=self.settings.run_worker_network,
            environment_allowlist=self.settings.run_worker_environment_allowlist,
        )
        self.coordinator = BackgroundCoordinator(self)
        self.services.memory_bridge.execute_run = self.execute_memory_run
        self._started = False
        self._closed = False
        self.traces: ApplicationTraceExporter | None = None
        self._configure_backends()
        dependencies.bind_providers(
            {
                "accounts": self.services.account_management,
                "audit": self.services.audit_management,
                "bots": self.services.bots,
                "message_delivery": self.services.bots,
                "configuration": self.services.configuration_management,
                "models": self.services.model_management,
                "sessions": self.services.conversation_management,
                "scheduling": self.services.scheduler,
                "triggers": self.services.triggers,
                "tool_invoker": self.services.tool_invoker,
                "skills": self.services.skill_management,
                "knowledge": self.services.knowledge_management,
                "embedding_registry": self.services.embedding_registry,
                "mcp_catalog": self.services.mcp_management,
                "agents": self.services.agent_management,
                "agent_factories": self.services.agent_factories,
                "runtime": self.services.run_management,
                "runtime_extensions": self.services.extensions,
                "code_tools": self.services.code_tools,
                "capabilities": self.services.capability_management,
                "script_tools": self.services.script_management,
                "sandbox_providers": self.services.sandbox_bridge,
                "workspaces": self.services.workspace_management,
                "memory": self.services.memory_management,
                "usage": self.services.usage_management,
                "notifications": self.services.notification_management,
                "outbox_management": self.services.outbox_management,
                "plugin_management": self.services.plugin_management,
            }
        )

    @property
    def is_started(self) -> bool:
        return self._started

    def _configure_backends(self) -> None:
        try:
            from opsmesh.modules.runtime.internal.backends.openai_agents.backend import (
                OpenAIAgentsBackend,
            )
        except ImportError:
            return  # Core without optional SDK still supports plugins registering other backends.
        self.services.extensions.register_backend(
            "openai_agents",
            lambda: OpenAIAgentsBackend(
                self.services.extensions, tracing_enabled=self.settings.tracing_enabled
            ),
        )
        if self.settings.tracing_enabled:
            from opsmesh.modules.runtime.internal.backends.openai_agents.tracing import (
                configure_sdk_tracing,
            )

            headers = (
                json.loads(self.services.secrets.resolve(self.settings.tracing_headers_secret_ref))
                if self.settings.tracing_headers_secret_ref
                else None
            )
            self.traces = ApplicationTraceExporter(
                self.settings.data_directory / "telemetry",
                endpoint=self.settings.tracing_endpoint,
                headers=headers,
                timeout_seconds=self.settings.tracing_timeout_seconds,
                queue_size=self.settings.tracing_queue_size,
            )
            configure_sdk_tracing(self.traces.provider)

    async def initialize(
        self, *, admin_username: str | None = None, admin_password: str | None = None
    ) -> None:
        self.settings.data_directory.mkdir(parents=True, exist_ok=True)
        await self.services.database.initialize()
        if admin_username is not None:
            if admin_password is None:
                raise OpsMeshError("CONFIG_INVALID", "An administrator password is required")
            if await self.services.accounts.list_users():
                raise OpsMeshError(
                    "CONFLICT", "Administrator initialization requires an empty user database"
                )
            await self.services.accounts.create_user(admin_username, admin_password, is_admin=True)

    async def start(self) -> None:
        if self._started:
            return
        if (self.settings.data_directory / ".restore-incomplete").exists():
            raise OpsMeshError(
                "RESTORE_INCOMPLETE", "Restore must be completed and verified before startup"
            )
        await self.services.database.verify_schema()
        for plugin in self.settings.plugins:
            load_plugin(plugin)
        await self.services.plugin_management.synchronize(force=True)
        await self.driver.start()
        self.driver.create_task(
            self.coordinator.synchronize_plugin_policy(), name="opsmesh-plugin-policy"
        )
        self.services.extensions.freeze()
        self.services.agent_factories.freeze()
        self.services.code_tools.freeze()
        self.services.knowledge.freeze()
        self._started = True
        if self.settings.worker_enabled:
            self.driver.create_task(self.coordinator.run(), name="opsmesh-coordinator")

    async def close(self) -> None:
        if self._closed:
            return
        errors: list[Exception] = []
        for close in (self.driver.stop, self.launcher.close, self.services.close):
            try:
                await close()
            except Exception as error:
                errors.append(error)
        try:
            if self.traces:
                await asyncio.to_thread(self.traces.close)
        finally:
            self._closed = True
        if errors:
            raise ExceptionGroup("Application shutdown requires attention", errors)

    async def create_workspace(self, principal_id: str, workspace_id: str) -> WorkspaceDescriptor:
        return await self._use_cases.create_workspace(principal_id, workspace_id)

    async def authorize_workspace(
        self, principal_id: str, workspace_id: str, *, write: bool = False
    ) -> None:
        return await self._use_cases.authorize_workspace(principal_id, workspace_id, write=write)

    async def restore_workspace(
        self, *, principal_id: str, snapshot_id: str, target_workspace_id: str
    ) -> WorkspaceDescriptor:
        return await self._use_cases.restore_workspace(
            principal_id=principal_id,
            snapshot_id=snapshot_id,
            target_workspace_id=target_workspace_id,
        )

    async def submit_run(
        self,
        *,
        principal_id: str,
        session_id: str,
        agent_ref: str,
        input: str | list[dict[str, Any]],
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
        overrides: RunOverrides | None = None,
    ) -> AgentRunView:
        return await self._use_cases.submit_run(
            principal_id=principal_id,
            session_id=session_id,
            agent_ref=agent_ref,
            input=input,
            idempotency_key=idempotency_key,
            metadata=metadata,
            overrides=overrides,
        )

    async def resume_run(
        self, *, principal_id: str, run_id: str, expected_revision: int
    ) -> AgentRunView:
        return await self._use_cases.resume_run(
            principal_id=principal_id, run_id=run_id, expected_revision=expected_revision
        )

    async def request_memory_generation(
        self, *, principal_id: str, namespace_id: str, idempotency_key: str
    ) -> MemoryJob:
        return await self._use_cases.request_memory_generation(
            principal_id=principal_id, namespace_id=namespace_id, idempotency_key=idempotency_key
        )

    async def execute_memory_run(self, run_id: str) -> AgentRunView:
        return await self._use_cases.execute_memory_run(run_id)

    async def dispatch(self, context: DispatchContext[OpsMeshEvent]) -> DispatchResult:
        return await self._use_cases.dispatch(context)
