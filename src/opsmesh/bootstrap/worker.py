"""Assemble the durable dispatch Worker: native Client plus feature Workflows.

Feature Workflows and Activities register here from S4 onward; the Outbox
consumer remains the only durable component the base process requires.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from temporalio.client import Client
from temporalio.worker import Worker

from opsmesh.bootstrap.connections import build_database_resources, secret_cipher, temporal_database
from opsmesh.bootstrap.extensions import (
    ExtensionModelAuthorization,
    ExtensionPublicAPI,
    ExtensionSandbox,
    ExtensionScopes,
    PackageEnvironmentOperations,
    RunExtensions,
)
from opsmesh.bootstrap.orchestration import (
    ConfigurationCleanupAuthorization,
    ConfigurationRunSources,
    ExecutionAssets,
    ExecutionModelConnections,
    NativeSessionWriter,
    execution_bindings,
)
from opsmesh.bootstrap.routers import (
    ActiveRunActivity,
    ConfigurationExecutionScopeAdmission,
    ConfigurationScopeAdmission,
)
from opsmesh.bootstrap.temporal import connect_temporal
from opsmesh.config import Settings
from opsmesh.extensions.activities import ExtensionActivities
from opsmesh.extensions.deployment import DockerPackageBuilder
from opsmesh.extensions.host import InvocationHost
from opsmesh.extensions.managed_activities import ManagedDeployments
from opsmesh.extensions.managed_process import DockerManagedWorker
from opsmesh.extensions.sandbox import PackageSandbox
from opsmesh.extensions.workflow import ExtensionWorkflow
from opsmesh.identity.provisioning import UserIdentityDirectory
from opsmesh.identity.quotas import service as quotas
from opsmesh.orchestration.runs.activities import RunActivities
from opsmesh.orchestration.runs.agent_workflow import ExecutionRunWorkflow
from opsmesh.orchestration.runs.assets import AssetActivities
from opsmesh.orchestration.runs.hooks import HookActivities
from opsmesh.orchestration.runs.mcp import RunMCPMeter
from opsmesh.orchestration.runs.model_activities import ExecutionModelProvider
from opsmesh.orchestration.runs.model_operations import ModelOperations
from opsmesh.orchestration.runs.model_workflow import ModelOperationWorkflow
from opsmesh.orchestration.runs.tool_activities import ToolActivities
from opsmesh.orchestration.runtime.activities import RuntimeActivities
from opsmesh.orchestration.runtime.mcp import MCPServerFactory
from opsmesh.orchestration.runtime.mcp_activities import MCPActivities
from opsmesh.orchestration.runtime.sandbox import SandboxBackend
from opsmesh.orchestration.runtime.workflow import ExecutionMCPWorkflow, ExecutionRuntimeWorkflow
from opsmesh.orchestration.sessions.activities import SessionActivities
from opsmesh.orchestration.sessions.history import HistoryGateway
from opsmesh.platform.model_connections.activities import ModelProviderActivities
from opsmesh.platform.model_connections.workflow import ModelProviderProbeWorkflow
from opsmesh.platform.operations.activities import PlatformOperationActivities
from opsmesh.platform.operations.admission import is_open
from opsmesh.platform.operations.workflow import PlatformOperationWorkflow
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.telemetry.context import restore_trace_context
from opsmesh.temporal.dispatch.contracts import DispatchRequest
from opsmesh.temporal.dispatch.dispatcher import TemporalOutboxDispatcher
from opsmesh.temporal.dispatch.outbox import TemporalOutboxPublisher


@dataclass(frozen=True)
class DispatchWorker:
    """The S4 worker runs the dispatch consumer and the feature Workflows."""

    dispatcher: TemporalOutboxDispatcher
    worker: Worker


@dataclass(frozen=True)
class FeatureRegistrations:
    """The feature Workflows and Activities one step registers on the Worker.

    The registration set is a fact of its own: a Workflow a service stages
    must be registered here or its durable execution never runs, so the
    set is assembled as data the assembly and the tests both read.
    """

    workflows: list[type] = field(default_factory=list)
    activities: list[Callable[..., Any]] = field(default_factory=list)


def feature_registrations(
    sessions: async_sessionmaker[AsyncSession],
    *,
    cipher: SecretCipher,
) -> FeatureRegistrations:
    """Assemble the Workflows and Activities of every delivered step."""
    model_provider_activities = ModelProviderActivities(cipher, sessions)
    return FeatureRegistrations(
        workflows=[
            ModelProviderProbeWorkflow,
        ],
        activities=[
            model_provider_activities.probe_connection,
        ],
    )


async def build_dispatch_worker(settings: Settings, resources: AsyncExitStack) -> DispatchWorker:
    database = await resources.enter_async_context(temporal_database(settings))
    cipher = secret_cipher(settings)
    execution_database = build_database_resources(settings)
    resources.callback(execution_database.engine.dispose)
    history = HistoryGateway(execution_database.sessions)
    from opsmesh.bootstrap.sandbox import SandboxDirectories

    admission = ConfigurationExecutionScopeAdmission()
    backend = SandboxBackend(
        SandboxDirectories(execution_database.sessions, Path(settings.storage_root), admission)
    )
    resources.callback(backend.close)
    bindings = execution_bindings()
    extension_scopes = ExtensionScopes(admission, cipher)
    run_extensions = RunExtensions(extension_scopes, cipher, execution_database.sessions)
    bindings = replace(
        bindings,
        extensions=run_extensions,
        sources=ConfigurationRunSources(cipher, admission, extension_scopes),
        cleanup=ConfigurationCleanupAuthorization(),
    )
    from opsmesh.bootstrap.orchestration import FlowOperationLimit
    from opsmesh.bootstrap.source_notifications import AuthoritativeEvents
    from opsmesh.orchestration.configurations.close_workflow import ConfigurationCloseWorkflow
    from opsmesh.orchestration.configurations.closing import ConfigurationActivities
    from opsmesh.orchestration.runs.activities import FlowActivities
    from opsmesh.orchestration.runs.flow_workflow import FlowWorkflow
    from opsmesh.orchestration.triggers.activities import TriggerActivities
    from opsmesh.orchestration.triggers.workflow import SourceEventWorkflow, TriggerWorkflow

    bindings = replace(bindings, limit_operation=FlowOperationLimit(cipher))
    run_activities = RunActivities(execution_database.sessions, admission, bindings, cipher)
    tool_activities = ToolActivities(run_activities, backend)
    runtime_activities = RuntimeActivities(
        execution_database.sessions, admission, bindings, cipher, backend, quotas
    )
    asset_activities = AssetActivities(
        run_activities,
        runtime_activities,
        ExecutionAssets(Path(settings.storage_root), ConfigurationScopeAdmission()),
    )
    session_activities = SessionActivities(
        execution_database.sessions, history, admission, ActiveRunActivity(), NativeSessionWriter()
    )
    provider = ExecutionModelProvider(
        execution_database.sessions,
        bindings,
        admission,
        ExecutionModelConnections(cipher, settings.execution_model_context_windows),
        cipher,
    )
    mcp_factory = MCPServerFactory(
        execution_database.sessions,
        admission,
        cipher,
        backend,
        RunMCPMeter(run_activities),
    )
    mcp_activities = MCPActivities(mcp_factory, bindings.cleanup)
    from fastapi import FastAPI

    from opsmesh.bootstrap.api import assemble_feature_services
    from opsmesh.bootstrap.routers import register_routers
    from opsmesh.shared.http.errors import register_error_handlers

    delegated_api = FastAPI()
    delegated_api.state.database_sessions = execution_database.sessions
    register_error_handlers(delegated_api)
    register_routers(delegated_api, prefix=settings.api_prefix)
    assemble_feature_services(delegated_api, settings, cipher)
    delegated_api.state.database_sessions = execution_database.sessions
    delegated_api.state.secret_cipher = cipher
    delegated_api.state.history_gateway = history
    delegated_api.state.json_cache = None
    scopes = ExtensionScopes(admission, cipher)
    model_operations = ModelOperations(provider, ExtensionModelAuthorization(scopes))
    delegated_api.state.execution_model_operations = model_operations
    extension_host = InvocationHost(
        execution_database.sessions,
        UserIdentityDirectory(),
        scopes,
        cipher,
        ExtensionPublicAPI(delegated_api, execution_database.sessions, scopes, cipher),
    )

    def reserve_deployment_capacity(session: Session, user_id: UUID, deployment_id: UUID) -> UUID:
        return bindings.capacity.reserve_capacity(
            session,
            user_id=user_id,
            kind="environment",
            amount=1,
            idempotency_scope="extensions.deployment",
            idempotency_key=str(deployment_id),
        ).id

    def release_deployment_capacity(session: Session, user_id: UUID, reservation_id: UUID) -> None:
        bindings.capacity.release_capacity(session, user_id=user_id, reservation_id=reservation_id)

    extensions = ExtensionActivities(
        execution_database.sessions,
        UserIdentityDirectory(),
        scopes,
        cipher,
        Path(settings.storage_root),
        DockerPackageBuilder(
            image=settings.extension_builder_image, sdk_wheel=Path(settings.extension_sdk_wheel)
        ),
        ExtensionSandbox(
            execution_database.sessions,
            PackageSandbox(PackageEnvironmentOperations(runtime_activities)),
        ),
        extension_host,
        api_prefix=settings.api_prefix,
        managed=ManagedDeployments(
            execution_database.sessions,
            UserIdentityDirectory(),
            scopes,
            DockerManagedWorker(),
            reserve=reserve_deployment_capacity,
            release=release_deployment_capacity,
        ),
    )
    run_extensions.activities = extensions
    hook_activities = HookActivities(bindings)
    client = await connect_temporal(
        settings, model_provider=provider, mcp_servers={"opsmesh": mcp_factory}
    )
    flows = FlowActivities(
        execution_database.sessions,
        UserIdentityDirectory(),
        scopes,
        admission,
        cipher,
        ExecutionAssets(Path(settings.storage_root), ConfigurationScopeAdmission()),
        bindings,
    )
    triggers = TriggerActivities(
        flows, AuthoritativeEvents(cipher, scopes), client, settings.temporal_task_queue
    )
    from opsmesh.bootstrap.orchestration import ConfigurationDrain

    closures = ConfigurationActivities(
        execution_database.sessions, cipher, ConfigurationDrain(cipher, bindings)
    )
    from opsmesh.bootstrap.platform import operation_handlers

    platform_operations = PlatformOperationActivities(
        execution_database.sessions, operation_handlers(cipher)
    )

    async def admit_start(request: DispatchRequest) -> bool:
        with execution_database.sessions() as admission_session, admission_session.begin():
            return admit_dispatch_start(admission_session, request)

    execution = FeatureRegistrations(
        workflows=[
            PlatformOperationWorkflow,
            FlowWorkflow,
            TriggerWorkflow,
            SourceEventWorkflow,
            ConfigurationCloseWorkflow,
            ExecutionRunWorkflow,
            ExecutionRuntimeWorkflow,
            ExecutionMCPWorkflow,
            ExtensionWorkflow,
            ModelOperationWorkflow,
        ],
        activities=[
            platform_operations.execute_page,
            platform_operations.failed,
            flows.load,
            flows.project,
            flows.begin_step,
            flows.probe_step,
            flows.complete_step,
            flows.cancel_steps,
            triggers.consume,
            triggers.sync,
            triggers.fire,
            closures.close,
            run_activities.prepare,
            run_activities.finish,
            run_activities.checkpoint,
            run_activities.apply_approval,
            tool_activities.invoke,
            runtime_activities.execute,
            session_activities.operate,
            asset_activities.transfer,
            asset_activities.collect,
            mcp_activities.operate,
            extensions.operate,
            model_operations.execute,
            hook_activities.invoke,
        ],
    )
    publisher = TemporalOutboxPublisher(
        database.sessions,
        client,
        task_queue=settings.temporal_task_queue,
        concurrency=settings.temporal_dispatch_concurrency,
        cipher=cipher,
        restore_context=restore_trace_context,
        admit_start=admit_start,
    )
    dispatcher = TemporalOutboxDispatcher(
        publisher,
        database_url=settings.database_url,
    )
    worker = build_feature_worker(
        client, settings, database.sessions, cipher=cipher, execution=execution
    )
    return DispatchWorker(dispatcher=dispatcher, worker=worker)


def admit_dispatch_start(session: Session, request: DispatchRequest) -> bool:
    if request.workflow_name == "opsmesh.platform.operation":
        return True
    if is_open(session):
        return True
    # A committed deployment may still be waiting for its first Workflow start
    # when drain closes admission. Its Activity observes drain and only stops it.
    return bool(
        request.workflow_name == "ExtensionWorkflow"
        and request.execution
        and request.execution.get("action") in {"start_worker", "stop_worker", "uninstall"}
    )


def build_feature_worker(
    client: Client,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    *,
    cipher: SecretCipher,
    execution: FeatureRegistrations,
) -> Worker:
    """Build the Worker over the registration set of every delivered step."""
    registrations = feature_registrations(sessions, cipher=cipher)
    return Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[*registrations.workflows, *execution.workflows],
        activities=[*registrations.activities, *execution.activities],
        max_concurrent_workflow_tasks=settings.temporal_workflow_concurrency,
        max_concurrent_activities=settings.temporal_activity_concurrency,
    )


async def run_dispatch_worker(settings: Settings, stop: asyncio.Event) -> None:
    async with AsyncExitStack() as resources:
        assembled = await build_dispatch_worker(settings, resources)
        worker_run = asyncio.create_task(assembled.worker.run(), name="opsmesh-feature-worker")
        dispatch_run = asyncio.create_task(
            assembled.dispatcher.run(stop), name="opsmesh-outbox-dispatcher"
        )
        try:
            finished, _ = await asyncio.wait(
                (worker_run, dispatch_run), return_when=asyncio.FIRST_COMPLETED
            )
            if worker_run in finished:
                await worker_run
                raise RuntimeError("Temporal Worker stopped before the dispatcher")
            await dispatch_run
        finally:
            stop.set()
            # Do not keep publishing when the Worker has failed. A cancelled
            # publication retains its durable intent and stable workflow ID.
            dispatch_run.cancel()
            await asyncio.gather(dispatch_run, return_exceptions=True)
            # A failed run has already shut down (or failed before startup).
            # Calling shutdown after pre-start validation fails can wait forever.
            if not worker_run.done():
                await assembled.worker.shutdown()
            await asyncio.gather(worker_run, return_exceptions=True)
