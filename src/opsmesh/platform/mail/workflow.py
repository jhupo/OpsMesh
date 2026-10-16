"""Durable mail delivery: bounded retries and honest unknown outcomes."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    # The activity entry description and its payloads are passed through
    # instead of being reloaded into every Workflow sandbox.
    from opsmesh.platform.mail.activities import DeliveryOutcome, MailActivities
    from opsmesh.platform.mail.service import MAIL_SEND_WORKFLOW, MailSendExecution

DELIVERY_START_TO_CLOSE_TIMEOUT = timedelta(seconds=120)
CANCELLATION_START_TO_CLOSE_TIMEOUT = timedelta(seconds=30)
DELIVERY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=300),
    maximum_attempts=8,
)


@workflow.defn(name=MAIL_SEND_WORKFLOW)
class MailSendWorkflow:
    """Deliver one registered intent; an unconfirmed send is never re-sent."""

    @workflow.run
    async def run(self, command: MailSendExecution) -> str:
        try:
            outcome: DeliveryOutcome = await workflow.execute_activity_method(
                MailActivities.deliver_mail_intent,
                command.intent_id,
                start_to_close_timeout=DELIVERY_START_TO_CLOSE_TIMEOUT,
                retry_policy=DELIVERY_RETRY_POLICY,
            )
        except asyncio.CancelledError:
            await self._record_cancellation(command.intent_id)
            raise
        if outcome.outcome_unknown:
            return "needs_attention"
        return "sent" if outcome.delivered else outcome.reason

    async def _record_cancellation(self, intent_id: UUID) -> None:
        try:
            await workflow.execute_activity_method(
                MailActivities.record_mail_cancellation,
                intent_id,
                start_to_close_timeout=CANCELLATION_START_TO_CLOSE_TIMEOUT,
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        except Exception:
            # A cancellation that cannot be recorded leaves the intent in its
            # last durable state; the operator reconciles it explicitly.
            workflow.logger.warning("Mail cancellation could not be recorded")
