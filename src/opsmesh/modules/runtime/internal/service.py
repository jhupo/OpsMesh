"""Durable run coordination with CAS ownership, approvals and session exclusion."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any, cast
from uuid import uuid4

from opsmesh.framework.agents import (
    AgentExecutionContext,
    AgentExecutionEvent,
    AgentResolver,
    AgentRunCompleted,
    AgentRunInterrupted,
    BackendOutcome,
    EventSink,
    OpsMeshAgentBackend,
    PendingToolApproval,
    bind_execution_context,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, StoredRecord, VersionedRecordStore
from opsmesh.modules.runtime.api import (
    TERMINAL_STATES,
    AgentRunView,
    ContextFactory,
    Finalizer,
    MetricObserver,
    RunApprovalPolicy,
    RunStreamEvent,
    RuntimeExtensions,
    SessionClaims,
    SubmitRunRequest,
    resolve_run_agent,
)
from opsmesh.modules.runtime.events import lifecycle_event

from .approvals import RunApprovalService
from .artifacts import merge_delegated_artifacts
from .cleanup import finish_run_cleanup
from .history import ModelHistory
from .metrics import RunMetrics
from .records import RunRecordStore, RunStreamStore, run_view
from .session_recovery import RunSessionRecovery
from .validation import validate_backend
from .workers import RunWorkerOwnership

logger = logging.getLogger(__name__)


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


class PersistentAgentRuntime:
    def __init__(
        self,
        database: AsyncDatabase,
        extensions: RuntimeExtensions,
        context_factory: ContextFactory,
        *,
        agent_resolver: AgentResolver | None = None,
        finalizer: Finalizer | None = None,
        session_claims: SessionClaims | None = None,
        observe_metric: MetricObserver | None = None,
    ) -> None:
        self.runs = RunRecordStore(database)
        self.claims = VersionedRecordStore(database, "runtime_session_claims")
        self.histories = VersionedRecordStore(database, "runtime_history")
        self.streams = RunStreamStore(database)
        self.extensions = extensions
        self.context_factory = context_factory
        self.agent_resolver = agent_resolver
        self.finalizer = finalizer
        self.session_claims = session_claims
        self.session_recovery = RunSessionRecovery(self.runs, self.claims, digest, session_claims)
        self.metrics = RunMetrics(observe_metric)
        self.approvals = RunApprovalService(self.runs, self.metrics, self.session_recovery)
        self.workers = RunWorkerOwnership(self.runs, self.metrics, self.session_recovery)
        self._tasks: dict[str, asyncio.Task[AgentRunView]] = {}

    async def get_run(self, run_id: str) -> AgentRunView:
        return run_view(await self.runs.require(run_id))

    async def find_submission(self, principal_id: str, idempotency_key: str) -> AgentRunView | None:
        record = await self.runs.get("run_" + digest([principal_id, idempotency_key])[:40])
        return run_view(record) if record else None

    async def get_run_input(self, run_id: str) -> str | list[dict[str, Any]]:
        # submit() validates this immutable field with SubmitRunRequest before persistence.
        return cast(str | list[dict[str, Any]], (await self.runs.require(run_id)).data["input"])

    async def list_runs(
        self,
        principal_id: str | None = None,
        *,
        statuses: tuple[str, ...] = (),
        session_id: str | None = None,
        limit: int = 1000,
        offset: int = 0,
        after_id: str | None = None,
    ) -> list[AgentRunView]:
        return [
            run_view(row)
            for row in await self.runs.select_runs(
                principal_id=principal_id,
                statuses=statuses,
                session_id=session_id,
                limit=limit,
                offset=offset,
                after_id=after_id,
            )
        ]

    async def count_runs(self, statuses: tuple[str, ...]) -> int:
        return await self.runs.count_statuses(statuses)

    async def submit(self, request: SubmitRunRequest) -> AgentRunView:
        # One deterministic ID scopes an idempotency key to its submitting principal.
        run_id = "run_" + digest([request.principal_id, request.idempotency_key])[:40]
        payload = request.model_dump(mode="json")
        identity = dict(payload, metadata=dict(payload["metadata"]))
        identity["metadata"].pop("trace_context", None)
        request_hash = digest(identity)
        existing = await self.runs.get(run_id)
        if existing:
            if existing.data["request_hash"] != request_hash:
                raise OpsMeshError("conflict", "Idempotency key used with different input")
            return run_view(existing)
        effective = resolve_run_agent(request.agent, request.overrides)
        validate_backend(effective, self.extensions.backend(effective.backend))
        data = dict(
            payload,
            agent=effective.model_dump(mode="json"),
            published_agent_ref=request.agent.ref,
            published_agent_fingerprint=digest(request.agent.model_dump(mode="json")),
            effective_agent_fingerprint=digest(effective.model_dump(mode="json")),
            status="queued",
            queued_at=time.time(),
            request_hash=request_hash,
            attempt=0,
            approvals=[],
            approval_policy=RunApprovalPolicy().model_dump(mode="json"),
            approval_reviews={},
            checkpoint=None,
            decisions={},
            usage={},
            cancel_requested=False,
        )
        try:
            record = await self.runs.create(
                run_id,
                data,
                events=[
                    lifecycle_event(run_id, "queued", {"session_id": request.session_id}),
                ],
            )
        except OpsMeshError as exc:
            if exc.code != "CONFLICT":
                raise
            return await self.submit(request)
        return run_view(record)

    def start(self, run_id: str, *, launch_token: str | None = None) -> asyncio.Task[AgentRunView]:
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            return task
        task = asyncio.create_task(
            self._execute_scheduled(run_id, launch_token), name=f"opsmesh:{run_id}"
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda completed: self._forget_task(run_id, completed))
        return task

    def _forget_task(self, run_id: str, task: asyncio.Task[AgentRunView]) -> None:
        if self._tasks.get(run_id) is task:
            self._tasks.pop(run_id, None)
        if not task.cancelled():
            task.exception()  # Retrieve exceptions; awaiting callers still receive them.

    async def _execute_scheduled(
        self, run_id: str, launch_token: str | None = None
    ) -> AgentRunView:
        while True:
            try:
                if launch_token is not None:
                    await self.renew_worker_launch(run_id, launch_token)
                return await self.execute(run_id, launch_token=launch_token)
            except OpsMeshError as error:
                if error.code == "OWNERSHIP_LOST":
                    return run_view(await self.runs.require(run_id))
                if error.code == "SESSION_BUSY" and error.retryable:
                    await asyncio.sleep(0.2)
                    continue
                record = await self.runs.require(run_id)
                if record.data["status"] not in {"queued", "resuming"}:
                    return run_view(record)  # A different worker already owns the run.
                if error.code == "CONFLICT":
                    await asyncio.sleep(0.2)
                    continue  # Session claim CAS raced with another run.
                record = await self._change(
                    run_id,
                    {"status": "failed", "error": error.info.model_dump(mode="json")},
                    launch_token=launch_token,
                    event="failed",
                )
                return run_view(record)

    async def close(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _claim_session(self, record: StoredRecord) -> None:
        if self.session_claims is not None:
            await self.session_claims.claim(record.data["session_id"], record.id)
            return
        key = digest(record.data["session_id"])
        claim = await self.claims.get(key)
        if claim is None:
            try:
                await self.claims.create(key, {"run_id": record.id})
                return
            except OpsMeshError as exc:
                if exc.code != "CONFLICT":
                    raise
                claim = await self.claims.get(key)
        if claim is None or claim.data["run_id"] != record.id:
            raise OpsMeshError("session_busy", "Another run owns this session", retryable=True)

    async def reconcile_session(self, run_id: str) -> bool:
        return await self.session_recovery.reconcile(run_id)

    async def _change(
        self,
        run_id: str,
        changes: dict[str, Any],
        *,
        attempt_id: str | None = None,
        launch_token: str | None = None,
        event: str | None = None,
    ) -> StoredRecord:
        for _ in range(8):
            record = await self.runs.require(run_id)
            if attempt_id is not None and record.data.get("attempt_id") != attempt_id:
                raise OpsMeshError("ownership_lost", "Run execution ownership changed")
            if record.data["status"] in TERMINAL_STATES:
                if attempt_id is not None:
                    raise OpsMeshError("OWNERSHIP_LOST", "Run execution has ended")
                return record
            if launch_token is not None and record.data.get("worker_launch_token") != launch_token:
                raise OpsMeshError("OWNERSHIP_LOST", "Worker launch ownership changed")
            if record.data.get("cancel_requested") and changes.get("status") in {
                "running",
                "finalizing",
                "completed",
                "waiting_approval",
            }:
                raise asyncio.CancelledError
            events = (
                [lifecycle_event(run_id, event, {"status": changes.get("status")})] if event else []
            )
            try:
                updated = await self.runs.update(
                    run_id, dict(record.data, **changes), record.revision, events=events
                )
                self.metrics.transition(record, updated)
                return updated
            except OpsMeshError as exc:
                if exc.code != "CONFLICT":
                    raise
        raise OpsMeshError("conflict", "Run is changing concurrently")

    async def _prepare_attempt(self, run_id: str, launch_token: str | None = None) -> StoredRecord:
        record = await self.runs.require(run_id)
        if record.data["status"] not in {"queued", "resuming"}:
            raise OpsMeshError("CONFLICT", "Run is not ready for execution")
        expected_token = record.data.get("worker_launch_token")
        if expected_token is not None and (
            launch_token != expected_token
            or (record.data.get("worker_launch_expires") or 0) <= time.time()
        ):
            raise OpsMeshError("OWNERSHIP_LOST", "Worker launch ownership changed or expired")
        if expected_token is None and launch_token is not None:
            raise OpsMeshError("OWNERSHIP_LOST", "Worker launch ownership was revoked")
        await self._claim_session(record)
        attempt_id = str(uuid4())
        changes = dict(
            record.data,
            status="preparing",
            attempt_id=attempt_id,
            attempt=record.data.get("attempt", 0) + 1,
            lease_expires=time.time() + 30,
        )
        try:
            updated = await self.runs.update(
                run_id,
                changes,
                record.revision,
                events=[lifecycle_event(run_id, "started", {"attempt_id": attempt_id})],
            )
        except BaseException:
            latest = await self.runs.require(run_id)
            if latest.data["status"] in TERMINAL_STATES:
                await self.session_recovery.release(latest)
            raise
        self.metrics.transition(record, updated)
        return updated

    async def _watch_attempt(self, record: StoredRecord, owner: asyncio.Task[Any]) -> None:
        last_heartbeat = time.monotonic()
        try:
            while True:
                await asyncio.sleep(0.2)
                latest = await self.runs.require(record.id)
                if (
                    latest.data.get("cancel_requested")
                    or latest.data["status"] in TERMINAL_STATES
                    or latest.data.get("attempt_id") != record.data["attempt_id"]
                ):
                    owner.cancel()
                    return
                if time.monotonic() - last_heartbeat >= 5:
                    await self._change(
                        record.id,
                        {"lease_expires": time.time() + 30},
                        attempt_id=record.data["attempt_id"],
                    )
                    last_heartbeat = time.monotonic()
        except Exception:
            owner.cancel()  # Stop executing when ownership can no longer be verified.

    def _event_sink(self, record: StoredRecord) -> EventSink:
        seq = int(record.data["attempt"]) * 1_000_000
        limit = seq + 999999

        async def emit(event: AgentExecutionEvent) -> None:
            nonlocal seq
            seq += 1
            if seq >= limit:
                raise OpsMeshError("RUN_LIMIT_EXCEEDED", "Too many stream events in one attempt")
            item = RunStreamEvent(
                run_id=record.id,
                attempt_id=record.data["attempt_id"],
                seq=seq,
                kind=event.kind,
                payload=event.payload,
            )
            await self.streams.create(f"{record.id}:{seq}", item.model_dump(mode="json"))

        return emit

    async def _execution_context(self, record: StoredRecord) -> AgentExecutionContext:
        expected = record.data.get("effective_agent_fingerprint")
        if expected and expected != digest(record.data["agent"]):
            raise OpsMeshError("CHECKPOINT_INCOMPATIBLE", "Effective run configuration changed")
        view = run_view(record)
        context = await self.context_factory(view)
        if (context.run_id, context.session_id, context.principal_id) != (
            view.id,
            view.session_id,
            view.principal_id,
        ):
            raise OpsMeshError("CONTEXT_INVALID", "Execution context identity mismatch")
        context.resolve_agent = context.resolve_agent or self.agent_resolver
        if context.state.model_history is None:
            context.state.model_history = ModelHistory(self.histories, view.session_id)
        context.state.usage_snapshot = view.usage.model_dump(mode="json")
        context.state.delegated_artifacts = merge_delegated_artifacts(
            view.metadata.get("delegated_artifacts", []),
            context.state.delegated_artifacts,
        )
        return context

    async def _persist_execution_metadata(
        self,
        record: StoredRecord,
        context: AgentExecutionContext,
        extra: dict[str, Any] | None = None,
    ) -> StoredRecord:
        metadata = dict(record.data.get("metadata", {}), **(extra or {}))
        receipts = merge_delegated_artifacts(
            metadata.get("delegated_artifacts", []),
            context.state.delegated_artifacts,
        )
        if receipts:
            metadata["delegated_artifacts"] = receipts
        if metadata == record.data.get("metadata", {}):
            return record
        return await self._change(
            record.id, {"metadata": metadata}, attempt_id=record.data["attempt_id"]
        )

    def _checkpoint_payload(
        self, record: StoredRecord, backend: OpsMeshAgentBackend
    ) -> dict[str, Any] | None:
        saved = record.data.get("checkpoint")
        if not saved:
            return None
        if (
            saved["spec_hash"] != digest(record.data["agent"])
            or saved["backend_version"] != backend.version
        ):
            raise OpsMeshError("CHECKPOINT_INCOMPATIBLE", "Agent definition or backend changed")
        if saved["digest"] != digest(saved["payload"]):
            raise OpsMeshError("CHECKPOINT_INVALID", "Checkpoint integrity check failed")
        payload: dict[str, Any] = saved["payload"]
        return payload

    async def _invoke(
        self, record: StoredRecord, context: AgentExecutionContext, backend: OpsMeshAgentBackend
    ) -> BackendOutcome:
        if record.data.get("final_outcome"):
            return AgentRunCompleted.model_validate(record.data["final_outcome"])
        view = run_view(record)
        checkpoint = self._checkpoint_payload(record, backend)
        await self._change(record.id, {"status": "running"}, attempt_id=view.attempt_id)
        async with asyncio.timeout(view.agent.timeout_seconds):
            return await backend.run(
                view.agent,
                record.data["input"],
                context,
                checkpoint=checkpoint,
                decisions=record.data.get("decisions", {}),
                emit=self._event_sink(record),
            )

    async def _interrupt(
        self, record: StoredRecord, outcome: AgentRunInterrupted, backend: OpsMeshAgentBackend
    ) -> StoredRecord:
        checkpoint = {
            "payload": outcome.checkpoint,
            "digest": digest(outcome.checkpoint),
            "spec_hash": digest(record.data["agent"]),
            "backend_version": backend.version,
        }
        return await self._change(
            record.id,
            {
                "status": "waiting_approval",
                "checkpoint": checkpoint,
                "decisions": {},
                "approvals": [a.model_dump(mode="json") for a in outcome.pending],
                "approval_reviews": {},
                "usage": outcome.usage.model_dump(mode="json"),
                "lease_expires": None,
                "approval_wait_started_at": time.time(),
            },
            attempt_id=record.data["attempt_id"],
            event="waiting_approval",
        )

    async def _finalize(self, record: StoredRecord, outcome: AgentRunCompleted) -> StoredRecord:
        record = await self._change(
            record.id,
            {
                "status": "finalizing",
                "final_outcome": outcome.model_dump(mode="json"),
                "usage": outcome.usage.model_dump(mode="json"),
            },
            attempt_id=record.data["attempt_id"],
        )
        view = run_view(record)
        if self.finalizer is None and view.agent.sandbox is not None:
            raise OpsMeshError("FINALIZER_REQUIRED", "Sandbox runs need an artifact finalizer")
        metadata = await self.finalizer(view, outcome) if self.finalizer else {}
        return await self._change(
            record.id,
            {
                "status": "completed",
                "output": outcome.output,
                "approvals": [],
                "checkpoint": None,
                "metadata": dict(view.metadata, **metadata),
                "final_outcome": None,
                "lease_expires": None,
            },
            attempt_id=view.attempt_id,
            event="completed",
        )

    async def _fail(
        self,
        record: StoredRecord,
        exception: Exception,
        context: AgentExecutionContext | None = None,
    ) -> StoredRecord:
        if isinstance(exception, OpsMeshError):
            error = exception
        elif isinstance(exception, TimeoutError):
            error = OpsMeshError("RUN_TIMEOUT", "Run deadline exceeded")
        else:
            error = OpsMeshError("RUNTIME_ERROR", "Run execution failed")
        status = "blocked" if error.code in {"GUARDRAIL_BLOCKED", "FORBIDDEN"} else "failed"
        changes: dict[str, Any] = {
            "status": status,
            "error": error.info.model_dump(mode="json"),
            "lease_expires": None,
        }
        if context is not None and not context.state.usage_snapshot.get("usage_available", True):
            changes["usage"] = context.state.usage_snapshot
        elif error.details and "usage" in error.details:
            changes["usage"] = error.details["usage"]
        elif context is not None and context.state.usage_snapshot:
            changes["usage"] = context.state.usage_snapshot
        return await self._change(
            record.id, changes, attempt_id=record.data["attempt_id"], event=status
        )

    async def _cancel_attempt(
        self, record: StoredRecord, usage: dict[str, Any] | None = None
    ) -> None:
        latest = await self.runs.require(record.id)
        if latest.data.get("attempt_id") != record.data["attempt_id"]:
            return  # A fenced-out worker cannot cancel its replacement.
        if latest.data["status"] in TERMINAL_STATES:
            await self.session_recovery.release(latest)
            return
        changes: dict[str, Any] = {
            "status": "cancelled",
            "cancel_requested": True,
            "lease_expires": None,
        }
        if usage is not None:
            changes["usage"] = usage
        latest = await self._change(
            record.id,
            changes,
            attempt_id=record.data["attempt_id"],
            event="cancelled",
        )
        await self.session_recovery.release(latest)

    async def _cleanup_attempt(
        self,
        record: StoredRecord,
        context: AgentExecutionContext | None,
        watcher: asyncio.Task[None],
        *,
        cancelled: bool,
    ) -> None:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
        try:
            if cancelled:
                await self._cancel_attempt(
                    record, context.state.usage_snapshot if context else None
                )
            elif record.data["status"] in TERMINAL_STATES:
                await self.session_recovery.release(record)
        finally:
            if context is not None and context.resources.cleanup_resources is not None:
                try:
                    await context.resources.cleanup_resources()
                except Exception:
                    logger.exception("Run resource cleanup needs retry: %s", record.id)

    async def execute(self, run_id: str, *, launch_token: str | None = None) -> AgentRunView:
        record = await self.runs.require(run_id)
        if record.data["status"] in TERMINAL_STATES:
            await self.session_recovery.release(record)
            return run_view(record)
        record = await self._prepare_attempt(run_id, launch_token)
        owner = asyncio.current_task()
        assert owner is not None
        watcher = asyncio.create_task(self._watch_attempt(record, owner))
        context: AgentExecutionContext | None = None
        cancelled = False
        try:
            context = await self._execution_context(record)
            with bind_execution_context(context):
                backend = self.extensions.backend(run_view(record).agent.backend)
                outcome = await self._invoke(record, context, backend)
                resource_metadata = None
                if isinstance(outcome, AgentRunInterrupted):
                    checkpoint_resources = context.resources.checkpoint_resources
                    if checkpoint_resources is not None:
                        resource_metadata = await checkpoint_resources()
                record = await self._persist_execution_metadata(record, context, resource_metadata)
                if isinstance(outcome, AgentRunInterrupted):
                    record = await self._interrupt(record, outcome, backend)
                else:
                    record = await self._finalize(record, outcome)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as error:
            record = await self._fail(record, error, context)
        finally:
            cancelled = cancelled or owner.cancelling() > 0
            if cancelled and context is not None:
                context.cancel_event.set()
            await finish_run_cleanup(
                self._cleanup_attempt(record, context, watcher, cancelled=cancelled)
            )
        return run_view(record)

    async def cancel(self, run_id: str, *, launch_token: str | None = None) -> AgentRunView:
        for _ in range(8):
            record = await self.runs.require(run_id)
            if launch_token is not None and record.data.get("worker_launch_token") != launch_token:
                return run_view(record)
            if record.data["status"] in TERMINAL_STATES:
                await self.session_recovery.release(record)
                return run_view(record)
            inactive = record.data["status"] in {"queued", "resuming", "waiting_approval"}
            changes = dict(record.data, cancel_requested=True)
            if inactive:
                changes["status"] = "cancelled"
            try:
                updated = await self.runs.update(
                    run_id,
                    changes,
                    record.revision,
                    events=[lifecycle_event(run_id, "cancelled", {})] if inactive else (),
                )
                self.metrics.transition(record, updated)
                record = updated
                break
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise
        else:
            raise OpsMeshError("CONFLICT", "Run is changing concurrently")
        if inactive:
            await self.session_recovery.release(record)
        task = self._tasks.get(run_id)
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        return run_view(record)

    async def list_approvals(self, run_id: str) -> tuple[PendingToolApproval, ...]:
        return await self.approvals.list_approvals(run_id)

    async def decide(
        self,
        run_id: str,
        approval_id: str,
        approved: bool,
        expected_revision: int,
        *,
        actor_id: str | None = None,
        reason: str = "",
    ) -> AgentRunView:
        return await self.approvals.decide(
            run_id, approval_id, approved, expected_revision, actor_id=actor_id, reason=reason
        )

    async def set_approval_policy(
        self,
        run_id: str,
        policy: RunApprovalPolicy,
        expected_revision: int,
        *,
        actor_id: str,
    ) -> AgentRunView:
        return await self.approvals.set_approval_policy(
            run_id, policy, expected_revision, actor_id=actor_id
        )

    async def expire_approvals(self, run_id: str, expected_revision: int) -> AgentRunView:
        return await self.approvals.expire_approvals(run_id, expected_revision)

    async def resume(self, run_id: str, expected_revision: int) -> AgentRunView:
        return await self.approvals.resume(run_id, expected_revision)

    async def recover(self, run_id: str, expected_revision: int) -> AgentRunView:
        return await self.workers.recover(run_id, expected_revision)

    async def recover_stale(self) -> list[AgentRunView]:
        return await self.workers.recover_stale()

    async def claim_worker_launch(self, run_id: str, token: str, lease_seconds: float = 30) -> bool:
        return await self.workers.claim_worker_launch(run_id, token, lease_seconds)

    async def renew_worker_launch(self, run_id: str, token: str, lease_seconds: float = 30) -> bool:
        return await self.workers.renew_worker_launch(run_id, token, lease_seconds)

    async def worker_exited(
        self, run_id: str, exit_code: int, *, launch_token: str | None = None
    ) -> AgentRunView:
        return await self.workers.worker_exited(run_id, exit_code, launch_token=launch_token)

    async def subscribe(self, run_id: str, after_seq: int = 0) -> AsyncIterator[RunStreamEvent]:
        cursor = after_seq
        while True:
            view = run_view(await self.runs.require(run_id))
            rows = await self.streams.read(run_id, cursor)
            for row in rows:
                event = RunStreamEvent.model_validate(row.data)
                cursor = event.seq
                yield event
            if len(rows) == 128:
                continue
            if view.status in TERMINAL_STATES or view.status == "waiting_approval":
                seq = view.attempt * 1_000_000 + 999999
                if cursor < seq:
                    yield RunStreamEvent(
                        run_id=run_id,
                        attempt_id=view.attempt_id,
                        seq=seq,
                        kind=view.status,
                        payload={
                            "revision": view.revision,
                            "output": view.output,
                            "error": view.error.model_dump(mode="json") if view.error else None,
                            "approvals": [item.model_dump(mode="json") for item in view.approvals],
                        },
                    )
                return
            await asyncio.sleep(0.1)
