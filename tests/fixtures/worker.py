"""Exercise the production asynchronous run executor and leased Worker entry point."""

import asyncio
from dataclasses import dataclass

import fakeredis
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.agents.execution.contracts import AgentRuntimeExecutor
from opsmesh.bootstrap.job_handlers import WorkerJobHandler
from opsmesh.orchestration.runs.async_execution import AsyncAgentRunExecutor
from opsmesh.runtime.instances.contracts import DockerRuntimeClient
from opsmesh.runtime.queues.contracts import JobPayload, JobType
from opsmesh.runtime.queues.dispatch import QueueDispatchPublisher
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.runtime.workers.dispatch_contracts import WorkerEventDispatchSummary
from opsmesh.runtime.workers.maintenance_contracts import WorkerMaintenanceSummary
from opsmesh.runtime.workers.models import WorkerRunnerConfig
from opsmesh.runtime.workers.nodes import WorkerHeartbeatOperationsService
from opsmesh.runtime.workers.runner import WorkerRunner
from opsmesh.shared.concurrency import BlockingIO
from opsmesh.shared.config import Settings
from opsmesh.shared.redis.keys import RedisKeyBuilder


@dataclass
class WorkerFlow:
    session: Session
    queue: RedisQueue | None = None
    agent_runner: AgentRuntimeExecutor | None = None
    settings: Settings | None = None
    runtime_docker_client: DockerRuntimeClient | None = None

    def __post_init__(self) -> None:
        self.queue = self.queue or RedisQueue(
            fakeredis.FakeRedis(decode_responses=True), RedisKeyBuilder("test-flow"), "agent_runs"
        )
        self.settings = self.settings or Settings(environment="test")
        self.factory = sessionmaker(bind=self.session.get_bind(), expire_on_commit=False)
        self.executor = AsyncAgentRunExecutor(
            self.factory, self.queue, self.settings, self.agent_runner, self.runtime_docker_client
        )

    def handle(self, job: JobPayload) -> None:
        self.session.commit()

        async def execute() -> None:
            with BlockingIO(1, name="test-io") as io, BlockingIO(1, name="test-control") as control:
                if job.job_type == JobType.AGENT_RUN:
                    await self.executor.handle(job, io, control)
                else:
                    await io.run(lambda: self._handle_product_job(job))

        try:
            asyncio.run(execute())
        finally:
            self.session.expire_all()

    def _handle_product_job(self, job: JobPayload) -> None:
        with self.factory() as session:
            WorkerJobHandler(
                session,
                self.queue,
                settings=self.settings,
                runtime_docker_client=self.runtime_docker_client,
            ).handle(job)
            session.commit()

    def process_next(self) -> bool:
        self.session.commit()
        QueueDispatchPublisher(self.session, self.queue).publish_pending()
        WorkerHeartbeatOperationsService(self.session).record_worker_heartbeat(
            worker_id="test-flow",
            worker_type="cloud",
            status="online",
            queue_name="agent_runs",
            details={},
            capacity={
                "max_jobs": 32,
                "capabilities": ["tools", "mcp", "sandbox"],
                "runtime_modes": ["isolated", "shared"],
                "cpu_count": 8,
                "memory_mb": 32768,
            },
        )
        self.session.commit()
        runner = WorkerRunner(
            queue=self.queue,
            session_factory=self.factory,
            config=WorkerRunnerConfig(worker_id="test-flow", retry_base_delay_seconds=0),
            handler_factory=lambda session: WorkerJobHandler(
                session,
                self.queue,
                settings=self.settings,
                runtime_docker_client=self.runtime_docker_client,
            ),
            async_handlers={JobType.AGENT_RUN: self.executor.handle},
            maintenance=lambda: WorkerMaintenanceSummary(recovered_runs=0, expired_leases=0),
            dispatch_events=lambda: WorkerEventDispatchSummary(),
            admission_blocked=lambda session: False,
            can_claim=lambda session, job: True,
            on_job_failure=lambda job, *, status, error: None,
            settings=self.settings,
        )
        try:
            return runner.run_once()
        finally:
            self.session.expire_all()
