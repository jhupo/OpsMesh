"""Injected service boundaries for application workflows, independent of bootstrap."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from opsmesh.framework.adapters import ReplyTarget
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.infrastructure.coordination.admission import AtomicRunAdmission
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.infrastructure.run_workers.status import WorkerProcessStatus
from opsmesh.infrastructure.secrets import EnvironmentSecretResolver
from opsmesh.infrastructure.telemetry.metrics import PlatformMetrics
from opsmesh.infrastructure.telemetry.tracing import ApplicationTraceExporter
from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.agents.api import AgentDefinitionCatalog
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.bots.api import BotAccounts, MessageDelivery
from opsmesh.modules.capabilities.api import (
    AgentResources,
    CapabilityCatalog,
    CodeToolRegistry,
    MCPServerCatalog,
    ScriptTools,
    SkillCatalog,
    ToolInvoker,
)
from opsmesh.modules.configuration.api import PlatformConfiguration
from opsmesh.modules.memory.api import MemoryService
from opsmesh.modules.models.api import ModelProfiles
from opsmesh.modules.operations.plugins import PluginDeploymentManagement
from opsmesh.modules.runtime.api import AgentRuntime, RuntimeExtensions
from opsmesh.modules.runtime.management import RunSubmission
from opsmesh.modules.sessions.api import Conversations
from opsmesh.modules.triggers.api import TriggerService
from opsmesh.modules.usage.api import UsageService
from opsmesh.modules.workspaces.api import WorkspaceAuthorization, WorkspaceService
from opsmesh.modules.workspaces.management import WorkspaceManagement

if TYPE_CHECKING:
    from .agent_bridge import AgentExecutionBridge
    from .authorization import PlatformAuthorization
    from .memory_tools import MemoryResourceBindings
    from .sandbox_bridge import SandboxWorkspaceBridge
    from .schedule_bridge import SchedulePlatformBridge


class ApplicationSettings(Protocol):
    @property
    def poll_interval_seconds(self) -> float: ...
    @property
    def budget_currency(self) -> str: ...
    @property
    def run_reservation_amount(self) -> str: ...
    @property
    def allow_trusted_local(self) -> bool: ...
    @property
    def memory_extract_model_profile(self) -> str | None: ...
    @property
    def memory_consolidate_model_profile(self) -> str | None: ...


class ApplicationBotServices(BotAccounts, MessageDelivery, Protocol):
    async def synchronize_adapters(self) -> None: ...


class ApplicationTriggerServices(TriggerService, Protocol):
    async def reconcile(self) -> int: ...


class NotificationObserver(Protocol):
    async def observe(
        self,
        *,
        event_id: str,
        event_type: str,
        owner_id: str,
        resource_id: str,
        status: str,
        summary: str = "",
        default_target: ReplyTarget | None = None,
    ) -> None: ...
    async def tick(self) -> None: ...


class ApplicationServices(Protocol):
    """Read-only collaborators assembled by the composition root.

    Properties allow implementations to provide narrower concrete service types
    without making application workflows import those private implementations.
    """

    @property
    def settings(self) -> ApplicationSettings: ...
    @property
    def database(self) -> AsyncDatabase: ...
    @property
    def admission(self) -> AtomicRunAdmission: ...
    @property
    def secrets(self) -> EnvironmentSecretResolver: ...
    @property
    def metrics(self) -> PlatformMetrics: ...
    @property
    def accounts(self) -> AccountDirectory: ...
    @property
    def agents(self) -> AgentDefinitionCatalog: ...
    @property
    def audit(self) -> AuditJournal: ...
    @property
    def authorization(self) -> PlatformAuthorization: ...
    @property
    def bots(self) -> ApplicationBotServices: ...
    @property
    def capabilities(self) -> CapabilityCatalog: ...
    @property
    def code_tools(self) -> CodeToolRegistry: ...
    @property
    def configuration(self) -> PlatformConfiguration: ...
    @property
    def extensions(self) -> RuntimeExtensions: ...
    @property
    def mcp_catalog(self) -> MCPServerCatalog: ...
    @property
    def memory(self) -> MemoryService: ...
    @property
    def memory_tools(self) -> MemoryResourceBindings: ...
    @property
    def models(self) -> ModelProfiles: ...
    @property
    def notifications(self) -> NotificationObserver: ...
    @property
    def resources(self) -> AgentResources: ...
    @property
    def runtime(self) -> AgentRuntime: ...
    @property
    def sandbox_bridge(self) -> SandboxWorkspaceBridge: ...
    @property
    def schedule_bridge(self) -> SchedulePlatformBridge: ...
    @property
    def scripts(self) -> ScriptTools: ...
    @property
    def sessions(self) -> Conversations: ...
    @property
    def skills(self) -> SkillCatalog: ...
    @property
    def tool_invoker(self) -> ToolInvoker: ...
    @property
    def triggers(self) -> ApplicationTriggerServices: ...
    @property
    def usage(self) -> UsageService: ...
    @property
    def workspace_owners(self) -> VersionedRecordStore: ...
    @property
    def workspaces(self) -> WorkspaceService: ...
    @property
    def workspace_management(self) -> WorkspaceManagement: ...
    @property
    def agent_bridge(self) -> AgentExecutionBridge: ...
    @property
    def plugin_management(self) -> PluginDeploymentManagement: ...


class RunWorkerController(Protocol):
    async def reap(self) -> None: ...
    async def poll(self, run_id: str) -> WorkerProcessStatus | None: ...
    async def launch(self, run_id: str) -> None: ...


class ApplicationHost(Protocol):
    @property
    def services(self) -> ApplicationServices: ...
    @property
    def settings(self) -> ApplicationSettings: ...
    @property
    def driver(self) -> OpsMeshDriver: ...
    @property
    def launcher(self) -> RunWorkerController: ...
    @property
    def traces(self) -> ApplicationTraceExporter | None: ...
    @property
    def is_started(self) -> bool: ...
    @property
    def submit_run(self) -> RunSubmission: ...
    @property
    def authorize_workspace(self) -> WorkspaceAuthorization: ...
