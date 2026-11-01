"""Independent durable queue loops with supervised delivery and lease renewal."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import nullcontext, suppress
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from opsmesh.framework.adapters import OpsMeshMessage, ReplyTarget
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import OpsMeshEvent
from opsmesh.framework.message import handle_event
from opsmesh.modules.runtime.api import TERMINAL_STATES

if TYPE_CHECKING:
    from .ports import ApplicationHost

logger = logging.getLogger(__name__)


class BackgroundCoordinator:
    def __init__(self, application: ApplicationHost) -> None:
        self.application = application
        self.worker_id = uuid4().hex
        self._memory_tasks: dict[str, asyncio.Task[Any]] = {}

    async def synchronize_plugin_policy(self) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await self.application.services.plugin_management.synchronize()
                await self.application.services.bots.synchronize_adapters()
            except Exception:
                logger.exception("Plugin policy synchronization failed")

    async def run(self) -> None:
        async with asyncio.TaskGroup() as group:
            for callback in (
                self.launch_runs,
                self.launch_memory_jobs,
                self.deliver_events,
                self.notifications,
            ):
                group.create_task(self._poll(callback), name=f"coordinator:{callback.__name__}")

    async def _poll(self, callback: Callable[[], Awaitable[None]]) -> None:
        while True:
            try:
                await callback()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Coordinator iteration failed")
            await asyncio.sleep(self.application.settings.poll_interval_seconds)

    async def tick(self) -> None:
        """One explicit iteration, also useful for deterministic integration tests."""
        await self.launch_runs()
        await self.launch_memory_jobs()
        await self.deliver_events()
        await self.notifications()

    async def notifications(self) -> None:
        services = self.application.services
        await services.triggers.reconcile()
        for run in await services.runtime.list_runs():
            if run.status != "waiting_approval" or run.approval_requested_at is None:
                continue
            deadline = run.approval_requested_at + timedelta(
                seconds=run.approval_policy.timeout_seconds
            )
            if datetime.now(UTC) >= deadline:
                try:
                    await services.runtime.expire_approvals(run.id, run.revision)
                except OpsMeshError as error:
                    if error.code != "CONFLICT":
                        raise
            elif datetime.now(UTC) >= run.approval_requested_at + timedelta(
                seconds=run.approval_policy.timeout_seconds / 2
            ):
                recipients = set(run.approval_policy.approver_ids)
                recipients.add(run.principal_id)
                for recipient in recipients:
                    await services.notifications.observe(
                        event_id=f"{run.id}:approval-reminder:{run.approval_requested_at.isoformat()}",
                        event_type="runtime.approval.reminder",
                        owner_id=recipient,
                        resource_id=run.id,
                        status="waiting_approval",
                    )
        await services.notifications.tick()

    async def launch_runs(self) -> None:
        services = self.application.services
        await self.application.launcher.reap()
        policy = await services.configuration.admission_policy()
        await services.runtime.recover_stale()
        runs = await services.runtime.list_runs()
        active = sum(run.status in {"preparing", "running", "finalizing"} for run in runs)
        services.metrics.gauge("active_runs", active)
        services.metrics.gauge(
            "queued_runs", sum(run.status in {"queued", "resuming"} for run in runs)
        )
        for run in runs:
            if run.status not in {"queued", "resuming"} or active >= policy.max_active_runs:
                continue
            process = await self.application.launcher.poll(run.id)
            if process is not None and process.status == "exited" and process.exit_code:
                current = await services.runtime.worker_exited(
                    run.id, process.exit_code, launch_token=process.launch_token
                )
                if current.status not in {"queued", "resuming"}:
                    continue
            await self.application.launcher.launch(run.id)
            active += 1

    async def launch_memory_jobs(self) -> None:
        services = self.application.services
        self._memory_tasks = {
            key: task for key, task in self._memory_tasks.items() if not task.done()
        }
        pending = await services.memory.list_pending_jobs()
        services.metrics.gauge("pending_memory_jobs", len(pending))
        for job in pending:
            if job.id not in self._memory_tasks:
                self._memory_tasks[job.id] = self.application.driver.create_task(
                    self._run_memory_job(job.id), name=f"memory:{job.id}"
                )

    async def _run_memory_job(self, job_id: str) -> None:
        started = perf_counter()
        try:
            job = await self.application.services.memory.run_job(job_id)
            outcome = "success" if job.status in {"succeeded", "no_change"} else "failure"
            self.application.services.metrics.increment("memory_jobs", outcome)
        except Exception:
            self.application.services.metrics.increment("memory_jobs", "failure")
            raise
        finally:
            self.application.services.metrics.observe(
                "memory_duration_seconds", perf_counter() - started
            )

    async def deliver_events(self) -> None:
        services = self.application.services
        owner = f"{self.worker_id}:{uuid4().hex}"
        events = await services.database.claim_events(owner, limit=10, lease_seconds=120)
        await asyncio.gather(*(self._deliver_claim(event, owner) for event in events))

    async def _deliver_claim(self, event: dict[str, Any], owner: str) -> None:
        database = self.application.services.database
        try:
            await self._deliver_with_lease(event, owner)
            await database.acknowledge_event(event["id"], owner=owner)
            self.application.services.metrics.increment("outbox_events")
        except asyncio.CancelledError:
            raise
        except Exception:
            with suppress(OpsMeshError):  # A lost lease belongs to the next delivery attempt.
                await database.reject_event(event["id"], owner=owner)
            self.application.services.metrics.increment("outbox_events", "failure")
            logger.exception("Outbox delivery failed", extra={"event_id": event["id"]})

    async def _deliver_with_lease(self, event: dict[str, Any], owner: str) -> None:
        finished = asyncio.Event()

        async def renew() -> None:
            while not finished.is_set():
                try:
                    await asyncio.wait_for(finished.wait(), timeout=30)
                except TimeoutError:
                    await self.application.services.database.renew_event_lease(
                        event["id"], owner, 120
                    )

        async def deliver() -> None:
            try:
                traces = self.application.traces
                scope = (
                    traces.span(
                        "event.deliver",
                        carrier=event.get("trace_context", {}),
                        attributes={"opsmesh.event.type": event["type"]},
                    )
                    if traces
                    else nullcontext()
                )
                with scope:
                    await self.deliver(event)
            finally:
                finished.set()

        async with asyncio.TaskGroup() as group:
            group.create_task(renew())
            group.create_task(deliver())

    async def deliver(self, fact: dict[str, Any]) -> None:
        services = self.application.services
        await services.audit.record(
            fact["type"],
            str(fact.get("correlation_id") or fact.get("subject") or fact.get("source", "")),
            event_id=fact["id"],
            actor_id=fact.get("actor_ref"),
            outcome="recorded",
            details=fact.get("payload", {}),
        )
        if fact["type"] in {
            "runtime.run.completed",
            "runtime.run.failed",
            "runtime.run.blocked",
            "runtime.run.cancelled",
        }:
            # Observed terminal notifications; financial accounting remains in the durable ledger.
            outcome = "success" if fact["type"].endswith(".completed") else "failure"
            services.metrics.increment("runs", outcome)
        run_id = fact.get("correlation_id")
        if fact["type"].startswith("runtime.run.") and run_id:
            run = await services.runtime.get_run(run_id)
            if fact["type"] == "runtime.run.queued":
                run_input = await services.runtime.get_run_input(run.id)
                text = (
                    run_input
                    if isinstance(run_input, str)
                    else json.dumps(run_input, ensure_ascii=False)
                )
                await services.sessions.append(
                    run.session_id,
                    "user",
                    text,
                    message_id=f"{run.id}:user",
                    run_id=run.id,
                    created_at=run.created_at,
                )
            if run.status in TERMINAL_STATES:
                await services.runtime.reconcile_session(run.id)
                await services.agent_bridge.settle(run)
            if fact["type"].rsplit(".", 1)[-1] in {
                *TERMINAL_STATES,
                "waiting_approval",
                "approval_expired",
            }:
                selected_target = None
                failed = fact["type"].rsplit(".", 1)[-1] in {
                    "failed",
                    "blocked",
                    "cancelled",
                    "approval_expired",
                }
                approval = fact["type"].endswith(".waiting_approval")
                if run.metadata.get("reply_target") and (
                    (failed and run.metadata.get("notify_on_failure", True))
                    or (approval and run.metadata.get("notify_on_approval", True))
                ):
                    selected_target = ReplyTarget.model_validate(run.metadata["reply_target"])
                await services.notifications.observe(
                    event_id=fact["id"],
                    event_type=fact["type"],
                    owner_id=run.principal_id,
                    resource_id=run.id,
                    status="blocked"
                    if fact["type"].endswith(".approval_expired")
                    else fact["type"].rsplit(".", 1)[-1],
                    default_target=selected_target,
                )
                if approval:
                    for approver_id in set(run.approval_policy.approver_ids) - {run.principal_id}:
                        await services.notifications.observe(
                            event_id=fact["id"],
                            event_type=fact["type"],
                            owner_id=approver_id,
                            resource_id=run.id,
                            status="waiting_approval",
                        )
            if fact["type"] == "runtime.run.completed" and run.metadata.get("reply_target"):
                target = ReplyTarget.model_validate(run.metadata["reply_target"])
                output = (
                    run.output
                    if isinstance(run.output, str)
                    else json.dumps(run.output, ensure_ascii=False)
                )
                await services.bots.send(
                    target,
                    OpsMeshMessage(output),
                    idempotency_key=f"{run.id}:reply",
                    actor_id=run.principal_id,
                )
        await handle_event(
            OpsMeshEvent(
                id=fact["id"],
                type=fact["type"],
                source=fact["source"],
                occurred_at=datetime.fromisoformat(fact["occurred_at"]),
                payload=fact.get("payload", {}),
            ),
            raise_on_error=True,
        )
