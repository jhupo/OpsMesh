"""The only composition root importing platform module implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from pydantic import SecretStr

if TYPE_CHECKING:
    from opsmesh.application.resource_references import PlatformResourceReferences
    from opsmesh.modules.accounts.internal.management import AuthorizedAccountManagement
    from opsmesh.modules.agents.management import AgentManagement
    from opsmesh.modules.audit.management import AuditManagement
    from opsmesh.modules.capabilities.embedding_registry import EmbeddingProviderRegistry
    from opsmesh.modules.capabilities.knowledge_management import KnowledgeManagement
    from opsmesh.modules.capabilities.management import CapabilityManagement
    from opsmesh.modules.capabilities.mcp_management import MCPManagement
    from opsmesh.modules.capabilities.script_management import ScriptManagement
    from opsmesh.modules.capabilities.skill_management import SkillManagement
    from opsmesh.modules.configuration.management import ConfigurationManagement
    from opsmesh.modules.memory.management import MemoryManagement
    from opsmesh.modules.models.management import ModelManagement
    from opsmesh.modules.notifications.internal.service import NotificationService
    from opsmesh.modules.notifications.management import NotificationManagement
    from opsmesh.modules.operations.outbox import OutboxManagement
    from opsmesh.modules.operations.plugins import PluginDeploymentManagement
    from opsmesh.modules.runtime.management import RunManagement
    from opsmesh.modules.sessions.management import ConversationManagement
    from opsmesh.modules.usage.management import UsageManagement
    from opsmesh.modules.workspaces.management import WorkspaceManagement

from opsmesh.application.agent_bridge import AgentExecutionBridge
from opsmesh.application.agent_context import (
    AgentContextBindings,
    ResourcePreparation,
    ResourceSynchronization,
)
from opsmesh.application.agent_validation import AgentPublicationValidator
from opsmesh.application.authorization import PlatformAuthorization
from opsmesh.application.memory_bridge import MemoryGenerationBridge
from opsmesh.application.memory_tools import MemoryResourceBindings
from opsmesh.application.run_finalization import RunResultFinalizer
from opsmesh.application.run_resources import RunResourceScope
from opsmesh.application.run_usage import RunUsageAccounting
from opsmesh.application.sandbox_bridge import SandboxWorkspaceBridge
from opsmesh.application.schedule_bridge import SchedulePlatformBridge
from opsmesh.application.trigger_bridge import TriggerPlatformBridge
from opsmesh.framework.agents import AgentExecutionContext
from opsmesh.framework.agents.resources import SandboxSessionFactory
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.infrastructure.coordination.admission import AtomicRunAdmission
from opsmesh.infrastructure.coordination.redis import RequestRateLimiter
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.infrastructure.secrets import DeploymentSecretResolver, FileCredentialCipher
from opsmesh.infrastructure.telemetry.metric_store import PersistentMetricStore
from opsmesh.infrastructure.telemetry.metrics import PlatformMetrics
from opsmesh.modules.accounts.internal.service import LocalAccountService
from opsmesh.modules.agents.internal.catalog import PersistentAgentDefinitionCatalog
from opsmesh.modules.agents.internal.factories import InProcessAgentFactoryRegistry
from opsmesh.modules.audit.internal.journal import PersistentAuditJournal
from opsmesh.modules.bots.api import BotEventDispatcher
from opsmesh.modules.bots.internal.conversations.inbox import PersistentConversationInboxProvider
from opsmesh.modules.bots.internal.service import BotService
from opsmesh.modules.capabilities.internal.catalog import PersistentCapabilityCatalog
from opsmesh.modules.capabilities.internal.code_tools import InProcessCodeToolRegistry
from opsmesh.modules.capabilities.internal.git_skills import GitSkillImporter
from opsmesh.modules.capabilities.internal.knowledge import LocalKnowledgeIndex
from opsmesh.modules.capabilities.internal.mcp import MCPServerConnector, MCPServerRegistry
from opsmesh.modules.capabilities.internal.resource_compiler import AgentResourceCompiler
from opsmesh.modules.capabilities.internal.scripts import PersistentScriptTools
from opsmesh.modules.capabilities.internal.skills import PersistentSkillCatalog
from opsmesh.modules.capabilities.internal.tool_invoker import AuthorizedToolInvoker
from opsmesh.modules.configuration.internal.service import PlatformConfigurationService
from opsmesh.modules.execution.api import ExecutionSpec
from opsmesh.modules.execution.internal.service import (
    DockerExecutionProvider,
    LocalExecutionProvider,
)
from opsmesh.modules.memory.internal.service import PersistentMemoryService
from opsmesh.modules.models.credentials import ModelCredentialManagement
from opsmesh.modules.models.internal.service import ModelProfileCatalog
from opsmesh.modules.notifications.internal.service import NotificationService
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from opsmesh.modules.runtime.internal.service import PersistentAgentRuntime
from opsmesh.modules.scheduling.internal.service import PersistentScheduler
from opsmesh.modules.sessions.internal.service import ConversationService
from opsmesh.modules.triggers.internal.service import PersistentTriggerService
from opsmesh.modules.usage.internal.service import PersistentUsageService
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService

from .bot_adapters import create_bot_adapters
from .management import compose_management
from .settings import OpsMeshSettings
from .storage import create_blob_store


class OpsMeshServices:
    settings: OpsMeshSettings
    database: AsyncDatabase
    admission: AtomicRunAdmission
    secrets: DeploymentSecretResolver
    model_credentials: ModelCredentialManagement
    http_client: httpx.AsyncClient
    metrics: PlatformMetrics
    metric_store: PersistentMetricStore
    rate_limiter: RequestRateLimiter
    accounts: LocalAccountService
    authorization: PlatformAuthorization
    sessions: ConversationService
    models: ModelProfileCatalog
    configuration: PlatformConfigurationService
    audit: PersistentAuditJournal
    workspaces: LocalWorkspaceService
    workspace_owners: VersionedRecordStore
    usage: PersistentUsageService
    code_tools: InProcessCodeToolRegistry
    capabilities: PersistentCapabilityCatalog
    skills: PersistentSkillCatalog
    mcp_catalog: MCPServerRegistry
    mcp: MCPServerConnector
    knowledge: LocalKnowledgeIndex
    scripts: PersistentScriptTools
    tool_invoker: AuthorizedToolInvoker
    resources: AgentResourceCompiler
    extensions: RuntimeExtensionRegistry
    agent_factories: InProcessAgentFactoryRegistry
    sandbox_bridge: SandboxWorkspaceBridge
    agent_bridge: AgentExecutionBridge
    agent_publication: AgentPublicationValidator
    run_finalizer: RunResultFinalizer
    run_usage: RunUsageAccounting
    agents: PersistentAgentDefinitionCatalog
    runtime: PersistentAgentRuntime
    memory_bridge: MemoryGenerationBridge
    memory: PersistentMemoryService
    memory_tools: MemoryResourceBindings
    bots: BotService
    schedule_bridge: SchedulePlatformBridge
    scheduler: PersistentScheduler
    trigger_bridge: TriggerPlatformBridge
    triggers: PersistentTriggerService
    account_management: AuthorizedAccountManagement
    agent_management: AgentManagement
    audit_management: AuditManagement
    configuration_management: ConfigurationManagement
    usage_management: UsageManagement
    model_management: ModelManagement
    capability_management: CapabilityManagement
    workspace_management: WorkspaceManagement
    memory_management: MemoryManagement
    knowledge_management: KnowledgeManagement
    notifications: NotificationService
    notification_management: NotificationManagement
    outbox_management: OutboxManagement
    plugin_management: PluginDeploymentManagement
    skill_management: SkillManagement
    script_management: ScriptManagement
    mcp_management: MCPManagement
    embedding_registry: EmbeddingProviderRegistry
    resource_references: PlatformResourceReferences
    run_management: RunManagement
    conversation_management: ConversationManagement

    def __init__(
        self,
        settings: OpsMeshSettings,
        driver: OpsMeshDriver,
        dispatch: BotEventDispatcher,
        secret_values: dict[str, SecretStr] | None = None,
    ) -> None:
        self.settings = settings
        self.database = AsyncDatabase(settings.resolved_database_url)
        self.admission = AtomicRunAdmission(self.database)
        self.secrets = DeploymentSecretResolver(secret_values)
        self.http_client = httpx.AsyncClient(timeout=30)
        self.metrics = PlatformMetrics()
        self.metric_store = PersistentMetricStore(self.database, self.metrics)
        driver.on_startup(self.metric_store.start)
        self.rate_limiter = RequestRateLimiter(settings.redis_url)
        self.accounts = LocalAccountService(self.database, token_ttl=settings.token_ttl_seconds)
        self.sessions = ConversationService(self.database)
        self.conversation_inboxes = PersistentConversationInboxProvider(self.database)
        self.models = ModelProfileCatalog(self.database)
        self.configuration = PlatformConfigurationService(self.database)
        self.audit = PersistentAuditJournal(self.database)
        self.model_credentials = ModelCredentialManagement(
            self.database,
            self.accounts,
            self.audit,
            FileCredentialCipher(settings.data_directory / "secrets" / "model-credentials.key"),
        )
        self.workspaces = LocalWorkspaceService(
            self.database,
            settings.data_directory / "storage",
            blob_store=create_blob_store(settings, self.secrets),
        )
        self.workspace_owners = VersionedRecordStore(self.database, "workspaces_owners")
        self.usage = PersistentUsageService(
            self.database, billing_currency=settings.budget_currency
        )
        self.code_tools = InProcessCodeToolRegistry()
        self.capabilities = PersistentCapabilityCatalog(self.database)
        self.authorization = PlatformAuthorization(
            accounts=self.accounts,
            models=self.models,
            capabilities=self.capabilities,
            workspace_owners=self.workspace_owners,
            settings=settings,
        )
        self._compose_capabilities()
        self._compose_runtime()
        self._compose_transports(driver, dispatch)
        compose_management(self)
        self.trigger_bridge = TriggerPlatformBridge(
            notifications=self.notifications,
            schedule_bridge=self.schedule_bridge,
            sessions=self.sessions,
        )
        self.triggers = PersistentTriggerService(
            VersionedRecordStore(self.database, "trigger_records"),
            self.authorization.check,
            self.agents.get_revision,
            self.trigger_bridge.submit,
            self.secrets.resolve,
            validate_target=self.trigger_bridge.validate,
            run_resolver=self.runtime.get_run,
            submission_resolver=self.runtime.find_submission,
        )

    def _compose_capabilities(self) -> None:
        settings = self.settings
        self.skills = PersistentSkillCatalog(
            self.database,
            self.workspaces,
            git_importer=GitSkillImporter(allowed_hosts=settings.skill_git_allowed_hosts),
        )
        self.mcp_catalog = MCPServerRegistry(self.database)
        self.mcp = MCPServerConnector(
            self.resolve_secret,
            self.authorization.capability,
            trusted_stdio=settings.allow_trusted_local,
            registry=self.mcp_catalog,
        )
        self.knowledge = LocalKnowledgeIndex(self.database, self.authorization.capability)
        script_provider = (
            LocalExecutionProvider()
            if settings.script_execution_provider == "local"
            else DockerExecutionProvider()
        )
        self.scripts = PersistentScriptTools(
            self.database,
            self.workspaces,
            self.capabilities,
            self.authorization.tool,
            execution_provider=script_provider,
            execution_template=ExecutionSpec(
                workspace=settings.data_directory,
                provider=settings.script_execution_provider,
                trusted_local=settings.allow_trusted_local,
                image=settings.script_execution_image
                if settings.script_execution_provider == "docker"
                else None,
                cpu=1 if settings.script_execution_provider == "docker" else None,
                memory_mb=512 if settings.script_execution_provider == "docker" else None,
                network="none" if settings.script_execution_provider == "docker" else "default",
            ),
        )
        self.tool_invoker = AuthorizedToolInvoker(
            self.database,
            self.capabilities,
            self.code_tools,
            self.authorization.tool,
            scripts=self.scripts,
        )
        self.resources = AgentResourceCompiler(
            self.capabilities,
            self.code_tools,
            self.skills,
            self.mcp_catalog,
            self.mcp,
            self.knowledge,
            self.workspaces,
            self.authorization.capability,
        )

    def _compose_runtime(self) -> None:
        settings = self.settings
        self.extensions = RuntimeExtensionRegistry()
        self.agent_factories = InProcessAgentFactoryRegistry()
        session_factory: SandboxSessionFactory | None = None
        try:
            from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_lifecycle import (
                ManagedSandboxSession,
            )
        except ImportError:
            pass  # The core can run without the optional OpenAI Agents SDK.
        else:
            session_factory = ManagedSandboxSession
        self.sandbox_bridge = SandboxWorkspaceBridge(
            self.workspaces,
            self.database,
            allow_trusted_local=settings.allow_trusted_local,
            session_factories={"openai_agents": session_factory}
            if session_factory is not None
            else {},
        )
        self.agents = PersistentAgentDefinitionCatalog(
            self.database,
            lambda spec: self.agent_publication.validate(spec),
            lambda spec, owner: self.agent_publication.prepare(spec, owner),
        )
        self.runtime = PersistentAgentRuntime(
            self.database,
            self.extensions,
            lambda run: self.agent_bridge.context(run),
            agent_resolver=self.agents.get_revision,
            finalizer=lambda run, outcome: self.run_finalizer.finalize(run, outcome),
            observe_metric=self.metrics.observe,
            session_claims=self.sessions,
        )
        self.memory_bridge = MemoryGenerationBridge(
            agents=self.agents,
            runtime=self.runtime,
            sessions=self.sessions,
            authorization=self.authorization,
            settings=settings,
        )
        self.memory = PersistentMemoryService(
            self.database,
            self.workspaces,
            self.memory_bridge.extract,
            self.memory_bridge.consolidate,
        )
        self.memory_tools = MemoryResourceBindings(
            memory=self.memory, capabilities=self.capabilities, code_tools=self.code_tools
        )
        self.run_usage = RunUsageAccounting(usage=self.usage, settings=settings)
        self.agent_publication = AgentPublicationValidator(
            agents=self.agents,
            capabilities=self.capabilities,
            code_tools=self.code_tools,
            extensions=self.extensions,
            mcp_catalog=self.mcp_catalog,
            models=self.models,
            authorization=self.authorization,
            model_credentials=self.model_credentials,
            scripts=self.scripts,
            settings=settings,
            skills=self.skills,
        )
        self.run_finalizer = RunResultFinalizer(
            sandbox=self.sandbox_bridge,
            sessions=self.sessions,
            workspaces=self.workspaces,
            workspace_owners=self.workspace_owners,
            memory=self.memory,
            usage=self.run_usage,
        )
        self.agent_bridge = AgentExecutionBridge(
            accounts=self.accounts,
            agents=self.agents,
            authorization=self.authorization,
            resources=self.resources,
            capabilities=self.capabilities,
            skills=self.skills,
            sessions=self.sessions,
            workspaces=self.workspaces,
            memory_tools=self.memory_tools,
            usage=self.run_usage,
            contexts=self._execution_context,
            scopes=self._resource_scope,
        )

    def _execution_context(
        self, run: AgentRunView, prepare: ResourcePreparation, sync: ResourceSynchronization
    ) -> AgentExecutionContext:
        return AgentContextBindings(
            run=run,
            usage=self.run_usage,
            prepare_resources=prepare,
            sync_resources=sync,
            agents=self.agents,
            authorization=self.authorization,
            metrics=self.metrics,
            model_credentials=self.model_credentials,
            models=self.models,
            secrets=self.secrets,
            settings=self.settings,
            tool_invoker=self.tool_invoker,
            ledger=self.usage,
        ).build()

    def _resource_scope(
        self, run: AgentRunView, context: AgentExecutionContext
    ) -> RunResourceScope:
        return RunResourceScope(
            run=run,
            context=context,
            admission=self.admission,
            configuration=self.configuration,
            memory_tools=self.memory_tools,
            resources=self.resources,
            sandbox_bridge=self.sandbox_bridge,
            workspace_owners=self.workspace_owners,
        )

    def _compose_transports(self, driver: OpsMeshDriver, dispatch: BotEventDispatcher) -> None:
        settings = self.settings
        self.bots = BotService(
            VersionedRecordStore(self.database, "bots_records"),
            self.secrets.resolve,
            self.authorization.check,
            dispatch,
            adapters=create_bot_adapters(
                driver, self.http_client, self.secrets.resolve, autostart=settings.worker_enabled
            ),
            agent_resolver=self.agents.get_revision,
            principal_resolver=self.accounts.get_principal,
        )
        self.notifications = NotificationService(
            self.database, self.accounts, self.audit, self.bots, self.authorization.check
        )
        self.schedule_bridge = SchedulePlatformBridge(
            accounts=self.accounts,
            agents=self.agents,
            authorization=self.authorization,
            bots=self.bots,
            notifications=self.notifications,
            sandbox_bridge=self.sandbox_bridge,
            sessions=self.sessions,
            workspaces=self.workspaces,
        )
        self.scheduler = PersistentScheduler(
            driver,
            VersionedRecordStore(self.database, "scheduler_records"),
            self.authorization.check,
            dispatch,
            autostart=settings.worker_enabled,
            agent_resolver=self.agents.get_revision,
            run_resolver=self.runtime.get_run,
            validate_target=self.schedule_bridge.validate,
            occurrence_observer=self.schedule_bridge.observe,
            submission_resolver=self.runtime.find_submission,
        )

    async def resolve_secret(self, reference: str) -> str:
        return self.secrets.resolve(reference)

    async def close(self) -> None:
        errors: list[Exception] = []
        for close in (
            self.runtime.close,
            self.bots.close,
            self.sandbox_bridge.close,
            self.workspaces.close,
            self.http_client.aclose,
            self.rate_limiter.close,
            self.metric_store.close,
            self.database.close,
        ):
            try:
                await close()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("Service shutdown requires attention", errors)
