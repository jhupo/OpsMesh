"""Project runtime facts into platform history, settlement and delivery."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from opsmesh.framework.adapters import OpsMeshMessage, ReplyTarget
from opsmesh.framework.events import OpsMeshEvent
from opsmesh.framework.message import handle_event
from opsmesh.infrastructure.telemetry.metrics import PlatformMetrics
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.bots.api import MessageDelivery
from opsmesh.modules.runtime.api import TERMINAL_STATES, AgentRuntime, AgentRunView
from opsmesh.modules.sessions.api import Conversations

from .ports import NotificationObserver


async def record_audit_fact(audit: AuditJournal, fact: dict[str, Any]) -> None:
    await audit.record(
        fact["type"],
        str(fact.get("correlation_id") or fact.get("subject") or fact.get("source", "")),
        event_id=fact["id"],
        actor_id=fact.get("actor_ref"),
        outcome="recorded",
        details=fact.get("payload", {}),
    )


async def publish_framework_event(fact: dict[str, Any]) -> None:
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


class RuntimeEventHandler:
    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        sessions: Conversations,
        settle: Callable[[AgentRunView], Awaitable[None]],
        notifications: NotificationObserver,
        delivery: MessageDelivery,
        metrics: PlatformMetrics,
    ) -> None:
        self.runtime, self.sessions, self.settle = runtime, sessions, settle
        self.notifications, self.delivery, self.metrics = notifications, delivery, metrics

    async def handle(self, fact: dict[str, Any]) -> None:
        if fact["type"] in {
            "runtime.run.completed",
            "runtime.run.failed",
            "runtime.run.blocked",
            "runtime.run.cancelled",
        }:
            # Observed terminal notifications; financial accounting remains in the durable ledger.
            outcome = "success" if fact["type"].endswith(".completed") else "failure"
            self.metrics.increment("runs", outcome)
        run_id = fact.get("correlation_id")
        if fact["type"].startswith("runtime.run.") and run_id:
            run = await self.runtime.get_run(run_id)
            if fact["type"] == "runtime.run.queued":
                run_input = await self.runtime.get_run_input(run.id)
                text = (
                    run_input
                    if isinstance(run_input, str)
                    else json.dumps(run_input, ensure_ascii=False)
                )
                await self.sessions.append(
                    run.session_id,
                    "user",
                    text,
                    message_id=f"{run.id}:user",
                    run_id=run.id,
                    created_at=run.created_at,
                )
            if run.status in TERMINAL_STATES:
                await self.runtime.reconcile_session(run.id)
                await self.settle(run)
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
                await self.notifications.observe(
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
                        await self.notifications.observe(
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
                await self.delivery.send(
                    target,
                    OpsMeshMessage(output),
                    idempotency_key=f"{run.id}:reply",
                    actor_id=run.principal_id,
                )
