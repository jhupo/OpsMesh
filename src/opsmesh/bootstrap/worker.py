"""Compose business job handlers and maintenance into the generic worker loop."""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from opsmesh.agents.execution.contracts import AgentRuntimeExecutor
from opsmesh.bootstrap.job_handlers import WorkerJobHandler
from opsmesh.capabilities.mcp.managed_maintenance import reconcile_managed_mcp
from opsmesh.orchestration.conversations.recovery import ConversationRecoveryService
from opsmesh.orchestration.runs.async_execution import AsyncAgentRunExecutor
from opsmesh.orchestration.scheduling.maintenance import WorkerMaintenanceService
from opsmesh.orchestration.tasks.event_outbox import TaskEventOutboxPublisher
from opsmesh.orchestration.tasks.events import RedisTaskEventBus
from opsmesh.platform.updates.service import maintenance_enabled
from opsmesh.runtime.instances.contracts import DockerRuntimeClient
from opsmesh.runtime.operations.admin_requests import AdminOperationService
from opsmesh.runtime.operations.history import PlatformHistoryService
from opsmesh.runtime.queues.contracts import JobType
from opsmesh.runtime.queues.dispatch import QueueDispatchPublisher
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.runtime.workers.dispatch_contracts import WorkerEventDispatchSummary
from opsmesh.runtime.workers.maintenance_contracts import (
    WorkerMaintenanceConfig,
    WorkerMaintenanceSummary,
)
from opsmesh.runtime.workers.models import WorkerRunnerConfig
from opsmesh.runtime.workers.runner import WorkerRunner
from opsmesh.shared.config import Settings, get_settings
from opsmesh.teams.execution.worker_failures import TeamWorkerFailureReporter


def build_worker_runner(
    *,
    queue: RedisQueue,
    session_factory: Callable[[], Session],
    config: WorkerRunnerConfig,
    agent_runner: AgentRuntimeExecutor | None = None,
    settings: Settings | None = None,
    runtime_docker_client: DockerRuntimeClient | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> WorkerRunner:
    @contextmanager
    def failure_session_scope() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def maintenance() -> WorkerMaintenanceSummary:
        try:
            with failure_session_scope() as session:
                if not maintenance_enabled(session):
                    reconcile_managed_mcp(session, queue)
        except Exception:
            logging.getLogger(__name__).error("Managed MCP maintenance failed")
        with failure_session_scope() as session:
            if not maintenance_enabled(session):
                ConversationRecoveryService(session).recover(limit=config.recovery_batch_size)
        try:
            with failure_session_scope() as session:
                PlatformHistoryService(session).capture(settings or get_settings())
        except Exception:
            logging.getLogger(__name__).error("Platform history sampling failed")
        try:
            for _ in range(5):
                with failure_session_scope() as session:
                    if not AdminOperationService(session).process_one(queue):
                        break
        except Exception:
            logging.getLogger(__name__).error("Platform operation maintenance failed")
        return WorkerMaintenanceService(
            queue=queue,
            session_factory=session_factory,
            config=WorkerMaintenanceConfig(
                run_lease_seconds=config.run_lease_seconds,
                recovery_batch_size=config.recovery_batch_size,
            ),
            settings=settings,
            runtime_docker_client=runtime_docker_client,
        ).run()

    def dispatch_events() -> WorkerEventDispatchSummary:
        with failure_session_scope() as session:
            if maintenance_enabled(session):
                return WorkerEventDispatchSummary()
            QueueDispatchPublisher(session, queue).publish_pending(limit=config.recovery_batch_size)
        with failure_session_scope() as session:
            result = TaskEventOutboxPublisher(
                session, RedisTaskEventBus(redis=queue.redis, key_prefix=queue.keys.prefix)
            ).publish_pending(limit=config.recovery_batch_size)
            return WorkerEventDispatchSummary(result.published, result.failed)

    failure_reporter = TeamWorkerFailureReporter(
        config=config,
        session_scope=failure_session_scope,
    )
    return WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=config,
        async_handlers={
            JobType.AGENT_RUN: AsyncAgentRunExecutor(
                session_factory=session_factory,
                queue=queue,
                settings=settings or get_settings(),
                docker_client=runtime_docker_client,
                agent_runner=agent_runner,
            ).handle
        },
        handler_factory=lambda session: WorkerJobHandler(
            session=session,
            queue=queue,
            settings=settings,
            runtime_docker_client=runtime_docker_client,
        ),
        maintenance=maintenance,
        dispatch_events=dispatch_events,
        admission_blocked=maintenance_enabled,
        on_job_failure=failure_reporter.record_failure,
        settings=settings,
        monotonic=monotonic,
        sleep=sleep,
    )
