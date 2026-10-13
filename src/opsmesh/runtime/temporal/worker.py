from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, cast

from temporalio.client import Client
from temporalio.openai_agents import (
    OpenAIAgentsPlugin,
    SandboxClientProvider,
)
from temporalio.worker import Worker

from opsmesh.agents.execution.providers.openai.activities import build_agent_activity_objects
from opsmesh.agents.execution.providers.openai.activity_policy import model_activity_parameters
from opsmesh.agents.execution.providers.openai.model_provider import build_model_provider
from opsmesh.agents.execution.providers.openai.sandbox_activities import AgentSandboxActivities
from opsmesh.agents.execution.providers.openai.sandbox_interceptor import (
    SandboxAdmissionInterceptor,
)
from opsmesh.agents.execution.providers.openai.workflow import WORKFLOWS as OPENAI_WORKFLOWS
from opsmesh.agents.providers.activities import ModelProviderHealthActivities
from opsmesh.agents.sessions.activities import SessionActivities
from opsmesh.capabilities.mcp.activities import ManagedMCPActivities
from opsmesh.capabilities.mcp.temporal_servers import build_temporal_mcp_servers
from opsmesh.capabilities.plugins.download_workflow import PluginDownloadWorkflow
from opsmesh.capabilities.plugins.downloads import PluginDownloadActivities
from opsmesh.capabilities.plugins.transport import PluginHTTPFetcher
from opsmesh.governance.audit.activities import AuditIntegrityActivities
from opsmesh.governance.credentials.activities import CredentialMaintenanceActivities
from opsmesh.messaging.channels.activities import ChannelDeliveryActivities
from opsmesh.orchestration.automations.activities import AutomationActivities
from opsmesh.orchestration.automations.workflows import (
    AutomationEventWorkflow,
    ReplyDeliveryWorkflow,
)
from opsmesh.orchestration.conversations.activities import ConversationActivities
from opsmesh.orchestration.conversations.workflow import ConversationWorkflow
from opsmesh.orchestration.definitions.activities import OrchestrationNodeActivities
from opsmesh.orchestration.definitions.workflow import OrchestrationNodeWorkflow
from opsmesh.orchestration.planning.activities import TaskPlanActivities
from opsmesh.orchestration.planning.workflow import TaskPlanWorkflow
from opsmesh.orchestration.runs.activities import RunExecutionActivities
from opsmesh.orchestration.scheduling.activities import ScheduleActivities
from opsmesh.orchestration.scheduling.workflows import ScheduleControlWorkflow, ScheduleTickWorkflow
from opsmesh.orchestration.webhooks.activities import WebhookDeliveryActivities
from opsmesh.platform.maintenance.activities import PlatformMaintenanceActivities
from opsmesh.platform.maintenance.workflow import (
    PlatformMaintenanceWorkflow,
    WorkspaceMaintenanceWorkflow,
)
from opsmesh.platform.operations.activities import AdminOperationActivities
from opsmesh.platform.operations.workflow import AdminOperationWorkflow
from opsmesh.resources.knowledge.activities import KnowledgeIngestionActivities
from opsmesh.resources.knowledge.url_fetch import HTTPKnowledgeURLFetcher
from opsmesh.resources.memory.activities import MemoryActivities
from opsmesh.resources.storage.backends import ObjectStorage
from opsmesh.resources.transfers.activities import ArchiveControlActivities
from opsmesh.runtime.sandboxes.activities import SandboxControlActivities
from opsmesh.runtime.sandboxes.registry import SandboxBackendRegistry
from opsmesh.runtime.sandboxes.workflow import WORKFLOWS as SANDBOX_WORKFLOWS
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.workflows import BACKGROUND_WORKFLOWS
from opsmesh.shared.config import Settings
from opsmesh.shared.db.session import SessionLocal
from opsmesh.teams.execution.activities import TeamOrchestrationActivities
from opsmesh.teams.execution.workflow import TeamOrchestrationWorkflow
from opsmesh.workspaces.projects.io.native import ProjectOutputActivities

REGISTERED_WORKFLOWS = (
    PluginDownloadWorkflow,
    *BACKGROUND_WORKFLOWS,
    *SANDBOX_WORKFLOWS,
    TaskPlanWorkflow,
    OrchestrationNodeWorkflow,
    ConversationWorkflow,
    AutomationEventWorkflow,
    ReplyDeliveryWorkflow,
    ScheduleControlWorkflow,
    ScheduleTickWorkflow,
    TeamOrchestrationWorkflow,
    PlatformMaintenanceWorkflow,
    WorkspaceMaintenanceWorkflow,
    AdminOperationWorkflow,
    *OPENAI_WORKFLOWS,
)


def build_temporal_worker(
    client: Client,
    *,
    settings: Settings,
    session_activities: SessionActivities,
    activity_executor: ThreadPoolExecutor,
    sandbox_backends: SandboxBackendRegistry,
    storage: ObjectStorage,
    knowledge_url_fetcher: Any | None = None,
    additional_workflows: Sequence[type] = (),
    additional_activities: Sequence[object] = (),
    mcp_servers: Mapping[str, Callable[..., Any]] | None = None,
    sandbox_clients: Sequence[SandboxClientProvider] | None = None,
) -> Worker:
    """Build a Worker borrowing resources kept alive until its Activities stop."""
    services = ActivityDependencies(sessions=SessionLocal, settings=settings)
    registered_mcp_servers = (
        mcp_servers
        if mcp_servers is not None
        else build_temporal_mcp_servers(SessionLocal, settings, sandbox_backends)
    )
    agent_activity_objects = build_agent_activity_objects(SessionLocal, settings)
    sandbox_admission = AgentSandboxActivities(SessionLocal, settings, sandbox_backends)
    activity_objects = (
        RunExecutionActivities(SessionLocal),
        PluginDownloadActivities(services, PluginHTTPFetcher()),
        AuditIntegrityActivities(services),
        PlatformMaintenanceActivities(services),
        AdminOperationActivities(services),
        CredentialMaintenanceActivities(services),
        ModelProviderHealthActivities(services),
        MemoryActivities(services),
        KnowledgeIngestionActivities(
            services,
            knowledge_url_fetcher
            or HTTPKnowledgeURLFetcher(timeout_seconds=settings.mcp_tool_timeout_seconds),
        ),
        ArchiveControlActivities(services),
        WebhookDeliveryActivities(services),
        AutomationActivities(services),
        ChannelDeliveryActivities(services),
        ScheduleActivities(services, client),
        TaskPlanActivities(services),
        ConversationActivities(SessionLocal),
        OrchestrationNodeActivities(services),
        ManagedMCPActivities(services, sandbox_backends),
        SandboxControlActivities(services, sandbox_backends),
        ProjectOutputActivities(services, sandbox_backends, storage),
        TeamOrchestrationActivities(services),
        *agent_activity_objects,
        sandbox_admission,
        session_activities,
    )
    activities: list[Callable[..., Any]] = [
        cast(Callable[..., Any], method)
        for instance in activity_objects
        for method in _activity_methods(instance)
    ]
    activities.extend(cast(Callable[..., Any], method) for method in additional_activities)
    registered_sandbox_clients: Sequence[SandboxClientProvider] = (
        cast(Sequence[SandboxClientProvider], list(sandbox_clients))
        if sandbox_clients is not None
        else cast(Sequence[SandboxClientProvider], sandbox_backends.temporal_providers())
    )
    plugin = OpenAIAgentsPlugin(
        model_provider=build_model_provider(SessionLocal, settings),
        model_params=model_activity_parameters(settings),
        mcp_servers=registered_mcp_servers,
        sandbox_clients=registered_sandbox_clients,
        # The Client's OTel plugin owns Temporal spans; Agents supplies only SDK spans.
        add_temporal_spans=False,
        use_otel_instrumentation=True,
    )
    return Worker(
        client,
        task_queue=settings.temporal_task_queue,
        identity=settings.temporal_worker_identity,
        workflows=[*REGISTERED_WORKFLOWS, *additional_workflows],
        activities=activities,
        plugins=[plugin],
        interceptors=[
            SandboxAdmissionInterceptor(
                sandbox_admission, tuple(provider.name for provider in registered_sandbox_clients)
            ),
        ],
        activity_executor=activity_executor,
        max_concurrent_workflow_tasks=settings.temporal_workflow_concurrency,
        max_concurrent_activities=settings.temporal_activity_concurrency,
        max_heartbeat_throttle_interval=timedelta(seconds=5),
        graceful_shutdown_timeout=timedelta(seconds=settings.temporal_shutdown_grace_seconds),
    )


def _activity_methods(instance: object) -> list[object]:
    return [
        getattr(instance, name)
        for name in dir(instance)
        if callable(getattr(instance, name))
        and getattr(getattr(instance, name), "__temporal_activity_definition", None) is not None
    ]
