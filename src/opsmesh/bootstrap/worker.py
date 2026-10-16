"""Assemble the durable dispatch Worker: native Client plus feature Workflows.

Feature Workflows and Activities register here from S4 onward; the Outbox
consumer remains the only durable component the base process requires.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.client import Client
from temporalio.worker import Worker

from opsmesh.bootstrap.resources import secret_cipher, temporal_database
from opsmesh.bootstrap.temporal import connect_temporal
from opsmesh.config import Settings
from opsmesh.governance.audit.activities import AuditActivities
from opsmesh.governance.audit.workflow import AuditVerificationWorkflow
from opsmesh.platform.mail.activities import MailActivities
from opsmesh.platform.mail.workflow import MailSendWorkflow
from opsmesh.shared.security.secrets import SecretCipher
from opsmesh.telemetry.context import restore_trace_context
from opsmesh.temporal.dispatch.dispatcher import TemporalOutboxDispatcher
from opsmesh.temporal.dispatch.outbox import TemporalOutboxPublisher


@dataclass(frozen=True)
class DispatchWorker:
    """The S4 worker runs the dispatch consumer and the feature Workflows."""

    dispatcher: TemporalOutboxDispatcher
    worker: Worker


async def build_dispatch_worker(settings: Settings, resources: AsyncExitStack) -> DispatchWorker:
    client = await connect_temporal(settings)
    database = await resources.enter_async_context(temporal_database(settings))
    cipher = secret_cipher(settings)
    publisher = TemporalOutboxPublisher(
        database.sessions,
        client,
        task_queue=settings.temporal_task_queue,
        concurrency=settings.temporal_dispatch_concurrency,
        cipher=cipher,
        restore_context=restore_trace_context,
    )
    dispatcher = TemporalOutboxDispatcher(
        publisher,
        database_url=settings.database_url,
    )
    worker = build_feature_worker(client, settings, database.sessions, cipher=cipher)
    return DispatchWorker(dispatcher=dispatcher, worker=worker)


def build_feature_worker(
    client: Client,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    *,
    cipher: SecretCipher,
) -> Worker:
    """Register the feature Workflows and Activities of the current step."""
    audit_activities = AuditActivities(sessions)
    mail_activities = MailActivities(cipher, sessions)
    return Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[AuditVerificationWorkflow, MailSendWorkflow],
        activities=[
            audit_activities.verify_audit_segment,
            audit_activities.record_audit_verification_progress,
            audit_activities.complete_audit_verification,
            mail_activities.deliver_mail_intent,
            mail_activities.record_mail_cancellation,
        ],
        max_concurrent_workflow_tasks=settings.temporal_workflow_concurrency,
        max_concurrent_activities=settings.temporal_activity_concurrency,
    )


async def run_dispatch_worker(settings: Settings, stop: asyncio.Event) -> None:
    async with AsyncExitStack() as resources:
        assembled = await build_dispatch_worker(settings, resources)
        worker_run = asyncio.create_task(assembled.worker.run(), name="opsmesh-feature-worker")
        try:
            await assembled.dispatcher.run(stop)
        finally:
            # The dispatcher drains its committed intents first; the Worker then
            # finishes in-flight Activities inside its shutdown grace period.
            await assembled.worker.shutdown()
            worker_run.cancel()
            with suppress(asyncio.CancelledError):
                await worker_run
