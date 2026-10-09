"""Compose business job handlers and maintenance into the generic worker loop."""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from backend.app.agents.execution.contracts import AgentRuntimeExecutor
from backend.app.bootstrap.job_handlers import WorkerJobHandler
from backend.app.capabilities.mcp.managed_maintenance import reconcile_managed_mcp
from backend.app.capabilities.mcp.transport.contracts import McpToolAdapter, McpToolAdapterResolver
from backend.app.orchestration.conversations.maintenance import ConversationMaintenanceService
from backend.app.orchestration.scheduling.maintenance import WorkerMaintenanceService
from backend.app.platform.updates.service import maintenance_enabled
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.runtime.operations.admin_requests import AdminOperationService
from backend.app.runtime.operations.history import PlatformHistoryService
from backend.app.runtime.queues.service import RedisQueue
from backend.app.runtime.workers.maintenance_contracts import (
    WorkerMaintenanceConfig,
    WorkerMaintenanceSummary,
)
from backend.app.runtime.workers.models import WorkerRunnerConfig
from backend.app.runtime.workers.runner import WorkerRunner
from backend.app.shared.config import Settings, get_settings
from backend.app.teams.execution.worker_failures import TeamWorkerFailureReporter


def build_worker_runner(
    *,
    queue: RedisQueue,
    session_factory: Callable[[], Session],
    config: WorkerRunnerConfig,
    agent_runner: AgentRuntimeExecutor | None = None,
    mcp_adapter: McpToolAdapter | McpToolAdapterResolver | None = None,
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
        for _ in range(5):
            with failure_session_scope() as session:
                if maintenance_enabled(session):
                    break
                if not ConversationMaintenanceService(session).process_one():
                    break
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

    failure_reporter = TeamWorkerFailureReporter(
        config=config,
        session_scope=failure_session_scope,
    )
    return WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=config,
        handler_factory=lambda session: WorkerJobHandler(
            session=session,
            queue=queue,
            agent_runner=agent_runner,
            settings=settings,
            mcp_adapter=mcp_adapter,
            runtime_docker_client=runtime_docker_client,
        ),
        maintenance=maintenance,
        admission_blocked=maintenance_enabled,
        on_job_failure=failure_reporter.record_failure,
        settings=settings,
        monotonic=monotonic,
        sleep=sleep,
    )
