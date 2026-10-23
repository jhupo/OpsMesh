"""Assemble the official Agents integration and engine adapters with the Outbox."""

from __future__ import annotations

import asyncio
import signal
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path

from temporalio import activity
from temporalio.openai_agents import SandboxClientProvider
from temporalio.worker import Worker

from opsmesh.agents.catalog.service import ManagedCatalog
from opsmesh.agents.providers.factory import CredentialResolver
from opsmesh.agents.providers.registry import ProviderRegistry
from opsmesh.bootstrap.automation import target_ports
from opsmesh.bootstrap.catalog import load_catalog
from opsmesh.bootstrap.connections import build_database_resources, secret_cipher, temporal_database
from opsmesh.bootstrap.execution import native_bindings_factory
from opsmesh.bootstrap.models import register_models
from opsmesh.bootstrap.plugins import create_plugin_manager, watch_plugins
from opsmesh.bootstrap.policies import PLATFORM_REDACTION
from opsmesh.bootstrap.telemetry import (
    configure_process_telemetry,
    flush_process_telemetry,
    shutdown_process_telemetry,
)
from opsmesh.bootstrap.temporal import connect_temporal
from opsmesh.bootstrap.workflows import WorkflowTools
from opsmesh.config import Settings, get_settings
from opsmesh.execution.runs.executor import BindingsFactory
from opsmesh.execution.runtime.sandbox import SandboxBackend
from opsmesh.execution.runtime.service import SandboxDirectories, SandboxOperations
from opsmesh.platform.evaluations.contracts import EvaluatorRegistry
from opsmesh.platform.evaluations.evaluators import default_evaluators
from opsmesh.resources.layout import StorageLayout
from opsmesh.resources.storage.local import LocalFileStorage
from opsmesh.telemetry.context import restore_trace_context
from opsmesh.telemetry.logging import configure_logging, shutdown_logging
from opsmesh.temporal.dispatch.dispatcher import TemporalOutboxDispatcher
from opsmesh.temporal.dispatch.outbox import TemporalOutboxPublisher
from opsmesh.workflows.engines.temporal.activities import WorkflowActivities
from opsmesh.workflows.engines.temporal.agent_activities import (
    InstanceActivities,
    InstanceModelProvider,
    InstanceSandboxClient,
)
from opsmesh.workflows.engines.temporal.agent_workflow import AgentInstanceWorkflow
from opsmesh.workflows.engines.temporal.evaluation_workflow import EvaluationWorkflow
from opsmesh.workflows.engines.temporal.evaluations import EvaluationActivities
from opsmesh.workflows.engines.temporal.sandbox_workflow import InstanceSandboxWorkflow
from opsmesh.workflows.engines.temporal.trigger_workflow import TriggerWorkflow
from opsmesh.workflows.engines.temporal.triggers import ScheduleReconciler, TriggerActivities
from opsmesh.workflows.engines.temporal.workflow import WorkflowExecutionWorkflow


@dataclass(frozen=True)
class DispatchWorker:
    dispatcher: TemporalOutboxDispatcher
    worker: Worker
    schedules: ScheduleReconciler


async def build_dispatch_worker(
    settings: Settings,
    resources: AsyncExitStack,
    *,
    instance_bindings: BindingsFactory | None = None,
    providers: ProviderRegistry | None = None,
    credentials: CredentialResolver | None = None,
    evaluators: EvaluatorRegistry | None = None,
) -> DispatchWorker:
    catalog = load_catalog(settings.agent_catalog_path)
    cipher = secret_cipher(settings)
    layout = StorageLayout(Path(settings.storage_root), settings.plugins_root)
    host = create_plugin_manager(settings, cipher, role="worker", layout=layout)
    resources.callback(host.close)
    await host.reload()
    database = await resources.enter_async_context(temporal_database(settings))
    local = build_database_resources(settings)
    resources.callback(local.engine.dispose)
    managed = ManagedCatalog(local.sessions, cipher)
    await asyncio.to_thread(managed.seed, catalog.agents.list(), catalog.providers.list())
    backend = SandboxBackend(
        SandboxDirectories(local.sessions, LocalFileStorage(layout.directories))
    )
    resources.callback(backend.close)
    sandbox = SandboxOperations(local.sessions, cipher, backend, settings.execution_sandbox_images)
    instances = InstanceActivities(
        local.sessions,
        cipher,
        history_root=layout.history,
        sandbox=sandbox,
        bindings=instance_bindings
        or native_bindings_factory(
            local.sessions,
            cipher,
            plugins=lambda: host.current.views["agents"],
            providers=(lambda: providers) if providers is not None else managed.providers,
            credentials=credentials if credentials is not None else managed.credential,
        ),
    )
    orchestration = WorkflowActivities(
        local.sessions,
        cipher,
        tools=WorkflowTools(local.sessions, plugins=lambda: host.current.views["agents"]),
    )
    targets = target_ports(cipher)
    triggers = TriggerActivities(local.sessions, cipher, targets)
    evaluation = EvaluationActivities(
        local.sessions, cipher, targets, evaluators or default_evaluators()
    )
    watch_plugins(host, resources)
    client = await connect_temporal(
        settings,
        model_provider=InstanceModelProvider(instances),
        mcp_servers={"opsmesh.instance": instances.mcp},
        sandbox_clients=(
            SandboxClientProvider(
                "opsmesh.instance",
                InstanceSandboxClient(
                    sandbox,
                    backend,
                    operation_id=lambda run_id: f"{run_id}:sandbox:{activity.info().activity_id}",
                    operation_scope=lambda run_id: (
                        f"{activity.info().activity_id}:{activity.info().attempt}"
                    ),
                ),
            ),
        ),
    )
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[
            AgentInstanceWorkflow,
            InstanceSandboxWorkflow,
            WorkflowExecutionWorkflow,
            TriggerWorkflow,
            EvaluationWorkflow,
        ],
        activities=[
            triggers.fire,
            triggers.result,
            triggers.cancel,
            evaluation.plan,
            evaluation.step,
            instances.claim,
            instances.finish,
            instances.decision,
            instances.abandon,
            instances.history,
            instances.tool,
            instances.callback,
            instances.sandbox_operation,
            orchestration.admit,
            orchestration.node_begin,
            orchestration.node_finish,
            orchestration.agent_submit,
            orchestration.agent_result,
            orchestration.agent_cancel,
            orchestration.tool_prepare,
            orchestration.tool,
            orchestration.signal_poll,
            orchestration.finish,
        ],
        max_concurrent_workflow_tasks=settings.temporal_workflow_concurrency,
        max_concurrent_activities=settings.temporal_activity_concurrency,
    )
    publisher = TemporalOutboxPublisher(
        database.sessions,
        client,
        task_queue=settings.temporal_task_queue,
        concurrency=settings.temporal_dispatch_concurrency,
        cipher=cipher,
        restore_context=restore_trace_context,
    )
    return DispatchWorker(
        TemporalOutboxDispatcher(publisher, database_url=settings.database_url),
        worker,
        ScheduleReconciler(database.sessions, cipher, client, settings.temporal_task_queue),
    )


async def run_dispatch_worker(settings: Settings, stop: asyncio.Event) -> None:
    async with AsyncExitStack() as resources:
        assembled = await build_dispatch_worker(settings, resources)
        worker_run = asyncio.create_task(assembled.worker.run(), name="opsmesh-feature-worker")
        dispatch_run = asyncio.create_task(
            assembled.dispatcher.run(stop), name="opsmesh-outbox-dispatcher"
        )
        schedules = asyncio.create_task(assembled.schedules.run(stop), name="opsmesh-schedules")
        try:
            finished, _ = await asyncio.wait(
                [worker_run, dispatch_run, schedules],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if worker_run in finished:
                await worker_run
                raise RuntimeError("Temporal Worker stopped before the dispatcher")
            if schedules in finished:
                await schedules
                if not stop.is_set():
                    raise RuntimeError("Schedule reconciliation stopped before the dispatcher")
            await dispatch_run
        finally:
            stop.set()
            # Do not keep publishing when the Worker has failed. A cancelled
            # publication retains its durable intent and stable workflow ID.
            dispatch_run.cancel()
            schedules.cancel()
            await asyncio.gather(dispatch_run, schedules, return_exceptions=True)
            # A failed run has already shut down (or failed before startup).
            # Calling shutdown after pre-start validation fails can wait forever.
            if not worker_run.done():
                await assembled.worker.shutdown()
            await asyncio.gather(worker_run, return_exceptions=True)


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        format=settings.log_format,
        queue_capacity=settings.log_queue_capacity,
        service_name=settings.service_name,
        environment=settings.environment,
        redaction=PLATFORM_REDACTION,
    )
    register_models()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    if hasattr(loop, "add_signal_handler"):
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stop.set)
            except NotImplementedError:
                break
    try:
        configure_process_telemetry(settings)
        await run_dispatch_worker(settings, stop)
    finally:
        flush_process_telemetry()
        shutdown_logging()
        shutdown_process_telemetry()
