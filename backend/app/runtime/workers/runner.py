from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from functools import partial
from threading import Event

from opentelemetry.trace import SpanKind
from sqlalchemy.orm import Session

from backend.app.runtime.queues.contracts import JobPayload, JobType
from backend.app.runtime.queues.execution_control import (
    ExecutionControl,
    ExecutionOwnershipLostError,
    current_execution_control,
    execution_control,
)
from backend.app.runtime.queues.service import QueueLease, RedisQueue
from backend.app.runtime.workers.capacity import WorkerCapacitySnapshotService, worker_can_run_job
from backend.app.runtime.workers.contracts import WorkerFailureHandler, WorkerJobTypeHandler
from backend.app.runtime.workers.maintenance_contracts import (
    WorkerMaintenanceSummary,
)
from backend.app.runtime.workers.models import WorkerRunnerConfig, WorkerRunSummary
from backend.app.runtime.workers.nodes import WorkerHeartbeatOperationsService
from backend.app.runtime.workers.reporting import WorkerLeaseReporter
from backend.app.runtime.workers.state import WorkerRunState
from backend.app.shared.concurrency import BlockingIO
from backend.app.shared.config import Settings
from backend.app.shared.telemetry.request_context import log_context
from backend.app.shared.telemetry.trace_context import (
    child_trace_context,
    current_trace_context,
    new_trace_context,
    telemetry_span,
    trace_context,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActiveExecution:
    lease: QueueLease
    control: ExecutionControl


@dataclass(frozen=True)
class ClaimedJob:
    lease: QueueLease
    execute: bool


class WorkerRunner:
    def __init__(
        self,
        *,
        queue: RedisQueue,
        session_factory: Callable[[], Session],
        config: WorkerRunnerConfig,
        handler_factory: Callable[[Session], WorkerJobTypeHandler],
        maintenance: Callable[[], WorkerMaintenanceSummary],
        admission_blocked: Callable[[Session], bool],
        on_job_failure: WorkerFailureHandler,
        settings: Settings | None = None,
        async_handlers: dict[
            JobType, Callable[[JobPayload, BlockingIO, BlockingIO], Awaitable[None]]
        ]
        | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._async_handlers = async_handlers or {}
        self._queue = queue
        self._session_factory = session_factory
        self._config = config
        self._handler_factory = handler_factory
        self._maintenance = maintenance
        self._admission_blocked = admission_blocked
        self._on_job_failure = on_job_failure
        self._settings = settings
        self._monotonic = monotonic
        self._sleep = sleep
        self._lease_reporter = WorkerLeaseReporter(
            config=config,
            session_scope=self._session_scope,
        )

    def _claim(self) -> ClaimedJob | None:
        # Only admission calls this, and only while a local asyncio Task slot is free.
        with self._session_scope() as session:
            if self._admission_blocked(session):
                return None
            capacity = WorkerCapacitySnapshotService(session).worker_capacity_snapshot(
                self._config.worker_id,
                default_max_jobs=self._config.concurrency,
            )
            if not capacity.accepting:
                return None
            worker_capacity = capacity.capacity or {}
            lease = self._queue.dequeue_matching_with_lease(
                lambda candidate: worker_can_run_job(candidate, worker_capacity),
                scan_limit=self._config.job_scan_limit,
            )
            if lease is None:
                return None
            try:
                execute = self._lease_reporter.start_lease(
                    lease.job,
                    claim_token=lease.lease_token,
                    session=session,
                )
                session.commit()
            except Exception as exc:
                session.rollback()
                self._queue.retry_or_dead_letter(
                    lease.job,
                    error=exc,
                    delay_seconds=self._retry_delay(lease.job),
                    lease_token=lease.lease_token,
                )
                raise
            if not execute:
                if not self._queue.ack(lease.job, lease_token=lease.lease_token):
                    raise RuntimeError("Duplicate terminal job could not be acknowledged")
                return ClaimedJob(lease=lease, execute=False)
            return ClaimedJob(lease=lease, execute=True)

    def run_once(self) -> bool:
        return asyncio.run(self.run_once_async())

    async def run_once_async(self) -> bool:
        with (
            BlockingIO(self._config.blocking_io_concurrency, name="opsmesh-io") as io,
            BlockingIO(2, name="opsmesh-control") as controls,
        ):
            claimed = await controls.run(self._claim)
            if claimed is None:
                return False
            if claimed.execute:
                control = ExecutionControl()
                active = ActiveExecution(claimed.lease, control)
                supervisor = asyncio.create_task(self._supervise(lambda: [active], controls))
                try:
                    await self._execute(claimed.lease, control, io, controls)
                finally:
                    supervisor.cancel()
                    with suppress(asyncio.CancelledError):
                        await supervisor
            return True

    async def _execute(
        self, lease: QueueLease, control: ExecutionControl, io: BlockingIO, controls: BlockingIO
    ) -> None:
        job = lease.job
        with self._job_log_context(job), execution_control(control):
            try:
                control.check_ownership()
                handler = self._async_handlers.get(job.job_type)
                if handler is not None:
                    await handler(job, io, controls)
                else:
                    await io.run(lambda: self._handle_job(job))
                control.check_ownership()
            except ExecutionOwnershipLostError:
                raise
            except Exception as exc:
                await controls.run(partial(self._settle_failure, lease, control, exc))
                raise
            await controls.run(lambda: self._settle_success(lease, control))

    def _settle_failure(
        self, lease: QueueLease, control: ExecutionControl, error: Exception
    ) -> None:
        job, claim_token = lease.job, lease.lease_token
        retry_job = (
            job
            if not control.cancel_requested.is_set()
            else job.model_copy(update={"max_attempts": job.attempt + 1})
        )
        status = "retrying" if retry_job.can_retry else "failed"
        with control.lease_transition:
            control.check_ownership()
            if not self._lease_reporter.finish_lease(
                job, claim_token=claim_token, status=status, metadata={"error": str(error)}
            ):
                raise ExecutionOwnershipLostError("Worker lease was lost on failure") from error
            self._on_job_failure(job, status=status, error=error)
            self._queue.retry_or_dead_letter(
                retry_job,
                error=error,
                delay_seconds=self._retry_delay(job),
                lease_token=claim_token,
            )
            control.settled.set()

    def _settle_success(self, lease: QueueLease, control: ExecutionControl) -> None:
        job, claim_token = lease.job, lease.lease_token
        with control.lease_transition:
            control.check_ownership()
            if not self._lease_reporter.finish_lease(
                job, claim_token=claim_token, status="completed"
            ):
                raise ExecutionOwnershipLostError("Worker lease was lost before completion")
            if not self._queue.ack(job, lease_token=claim_token):
                raise ExecutionOwnershipLostError("Queue lease was lost before acknowledgement")
            control.settled.set()

    async def _supervise(
        self, active: Callable[[], list[ActiveExecution]], controls: BlockingIO
    ) -> None:
        interval = self._config.heartbeat_interval_seconds
        if interval <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            snapshot = active()
            await controls.run(partial(self._pulse, snapshot))

    def _pulse(self, active: list[ActiveExecution]) -> None:
        for execution in active:
            lease = execution.lease
            with execution.control.lease_transition:
                if execution.control.settled.is_set():
                    continue
                try:
                    owned = self._queue.heartbeat(lease.job, lease_token=lease.lease_token)
                    if owned:
                        owned = self._lease_reporter.heartbeat_lease(
                            lease.job,
                            claim_token=lease.lease_token,
                        )
                    if not owned:
                        execution.control.ownership_lost.set()
                except Exception:
                    execution.control.ownership_lost.set()
                    logger.exception("Worker lease heartbeat failed")

    def _handle_job(self, job: JobPayload) -> None:
        with self._session_scope() as session:
            handler = self._handler_factory(session)
            handler.handle(job)

    @contextmanager
    def _job_log_context(self, job: JobPayload) -> Iterator[None]:
        run_id = (
            job.resource_id if job.job_type.value in {"agent.run", "mcp.tool_execution"} else None
        )
        parent_trace = job.trace_context()
        runtime_id = job.routing.get("runtime_id") or job.routing.get("workspace_runtime_id")
        task_id = job.routing.get("task_id")
        if not self._tracing_enabled():
            active_trace = child_trace_context(parent_trace)
            with (
                trace_context(active_trace),
                log_context(
                    request_id=job.request_id,
                    worker_id=self._config.worker_id,
                    workspace_id=job.workspace_id,
                    task_id=task_id,
                    run_id=run_id,
                    runtime_id=runtime_id,
                    **active_trace.metadata(),
                ),
            ):
                yield
            return
        with (
            telemetry_span(
                "opsmesh.worker.process_job",
                parent=parent_trace,
                kind=SpanKind.CONSUMER,
                attributes={
                    "messaging.destination.name": self._config.queue_name,
                    "messaging.operation.name": "process",
                    "messaging.system": "redis",
                    "opsmesh.job.attempt": job.attempt,
                    "opsmesh.job.type": job.job_type.value,
                    "opsmesh.workspace.id": str(job.workspace_id),
                },
            ) as active_trace,
            log_context(
                request_id=job.request_id,
                worker_id=self._config.worker_id,
                workspace_id=job.workspace_id,
                task_id=task_id,
                run_id=run_id,
                runtime_id=runtime_id,
                **active_trace.metadata(),
            ),
        ):
            yield

    def run(
        self, *, max_jobs: int | None = None, stop_event: Event | None = None
    ) -> WorkerRunSummary:
        return asyncio.run(self.run_async(max_jobs=max_jobs, stop_event=stop_event))

    async def run_async(
        self, *, max_jobs: int | None = None, stop_event: Event | None = None
    ) -> WorkerRunSummary:
        if max_jobs is not None and max_jobs < 1:
            raise ValueError("max_jobs must be positive")
        state = WorkerRunState()
        active: dict[asyncio.Task[None], ActiveExecution] = {}
        next_heartbeat_at = 0.0
        next_maintenance_at = 0.0
        maintenance: asyncio.Task[WorkerMaintenanceSummary] | None = None
        claimed = 0
        initialized = False
        health_status = "online"
        with (
            BlockingIO(self._config.blocking_io_concurrency, name="opsmesh-io") as io,
            BlockingIO(2, name="opsmesh-control") as controls,
            BlockingIO(1, name="opsmesh-maintenance") as upkeep,
        ):
            supervisor = asyncio.create_task(
                self._supervise(lambda: list(active.values()), controls),
                name="worker-lease-renewal",
            )
            try:
                while True:
                    stopping = self._is_stopped(stop_event)
                    if stopping:
                        for execution in active.values():
                            execution.control.cancel_requested.set()
                    for task in list(active):
                        if not task.done():
                            continue
                        active.pop(task)
                        try:
                            task.result()
                            state.processed += 1
                            health_status = "online"
                        except Exception as exc:
                            state.failed += 1
                            state.last_error = str(exc)
                            health_status = "degraded"
                            logger.error("Worker job failed", exc_info=exc)
                    if maintenance is not None and maintenance.done():
                        try:
                            state.record_maintenance(maintenance.result())
                        except Exception as exc:
                            state.last_error = str(exc)
                            logger.error("Worker maintenance failed", exc_info=exc)
                        maintenance = None
                        initialized = True
                        next_maintenance_at = self._monotonic() + max(
                            self._config.maintenance_interval_seconds, 0.01
                        )
                    now = self._monotonic()
                    if now >= next_heartbeat_at:
                        details = state.heartbeat_details(self._config)
                        details.update(
                            active_jobs=len(active),
                            available_slots=self._config.concurrency - len(active),
                        )
                        await controls.run(
                            partial(
                                self.record_heartbeat,
                                "stopping" if stopping else health_status,
                                details,
                            )
                        )
                        next_heartbeat_at = (
                            now + self._config.heartbeat_interval_seconds
                            if self._config.heartbeat_interval_seconds > 0
                            else float("inf")
                        )
                    exhausted = max_jobs is not None and claimed >= max_jobs
                    if stopping or exhausted:
                        if not active and maintenance is None:
                            break
                    else:
                        if maintenance is None and now >= next_maintenance_at:
                            maintenance = asyncio.create_task(upkeep.run(self.run_maintenance))
                        if maintenance is not None and not initialized:
                            await asyncio.wait([maintenance], timeout=0.01)
                            continue
                        if len(active) < self._config.concurrency:
                            try:
                                candidate = await controls.run(self._claim)
                            except Exception as exc:
                                claimed += 1
                                state.failed += 1
                                state.last_error = str(exc)
                                health_status = "degraded"
                                await io.run(
                                    lambda: self._sleep(max(self._config.idle_sleep_seconds, 0.01))
                                )
                                continue
                            if candidate is not None:
                                claimed += 1
                                if not candidate.execute:
                                    state.processed += 1
                                else:
                                    control = ExecutionControl()
                                    execution = ActiveExecution(candidate.lease, control)
                                    task = asyncio.create_task(
                                        self._execute(candidate.lease, control, io, controls),
                                        name=f"job:{candidate.lease.job.job_id}",
                                    )
                                    active[task] = execution
                                continue
                            state.idle_polls += 1
                            if not active:
                                await io.run(
                                    lambda: self._sleep(max(0.01, self._config.idle_sleep_seconds))
                                )
                    timeout = min(0.1, max(0.01, self._config.idle_sleep_seconds))
                    waiting: set[asyncio.Task[None] | asyncio.Task[WorkerMaintenanceSummary]] = set(
                        active
                    )
                    if maintenance is not None:
                        waiting.add(maintenance)
                    if waiting:
                        await asyncio.wait(
                            waiting, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                        )
                    else:
                        await asyncio.sleep(timeout)
            finally:
                # External coroutine cancellation also requests cooperative stop. Do not
                # orphan active adapters or abandon an owned queue lease.
                for execution in active.values():
                    execution.control.cancel_requested.set()
                if active:
                    await asyncio.gather(*active, return_exceptions=True)
                if maintenance is not None:
                    await maintenance
                supervisor.cancel()
                with suppress(asyncio.CancelledError):
                    await supervisor
            await controls.run(
                lambda: self.record_heartbeat(
                    "stopping" if self._is_stopped(stop_event) else health_status,
                    state.heartbeat_details(self._config),
                )
            )
        return state.summary(stopped=self._is_stopped(stop_event))

    def record_heartbeat(self, status: str, details: dict[str, object]) -> None:
        details = self._heartbeat_trace_details(details)
        details = dict(
            details,
            execution_backend="asyncio",
            blocking_io_concurrency=self._config.blocking_io_concurrency,
        )
        try:
            with self._session_scope() as session:
                WorkerHeartbeatOperationsService(session).record_worker_heartbeat(
                    worker_id=self._config.worker_id,
                    worker_type=self._config.worker_type,
                    status=status,
                    queue_name=self._config.queue_name,
                    details=details,
                    capacity={"max_jobs": self._config.concurrency},
                )
        except Exception:
            logger.exception("Failed to record worker heartbeat")

    def _heartbeat_trace_details(self, details: dict[str, object]) -> dict[str, object]:
        if not self._tracing_enabled():
            return details
        context = current_trace_context() or new_trace_context()
        return dict(details) | context.metadata()

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            control = current_execution_control()
            if control is not None:
                control.check_ownership()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _is_stopped(self, stop_event: Event | None) -> bool:
        return stop_event is not None and stop_event.is_set()

    def run_maintenance(self) -> WorkerMaintenanceSummary:
        return self._maintenance()

    def _retry_delay(self, job: JobPayload) -> float:
        if not job.can_retry:
            return 0.0
        base_delay = max(0.0, self._config.retry_base_delay_seconds)
        if base_delay <= 0:
            return 0.0
        delay = float(base_delay * (2 ** max(0, job.attempt)))
        max_delay = self._config.retry_max_delay_seconds
        return min(delay, max_delay) if max_delay > 0 else delay

    def _tracing_enabled(self) -> bool:
        return self._settings.tracing_enabled if self._settings is not None else True
