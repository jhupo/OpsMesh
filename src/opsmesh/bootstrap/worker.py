"""Assemble the durable dispatch Worker: native Client plus feature Workflows.

Feature Workflows and Activities register here from S4 onward; the Outbox
consumer remains the only durable component the base process requires.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.client import Client
from temporalio.worker import Worker

from opsmesh.bootstrap.mail import mail_source_authorizations
from opsmesh.bootstrap.resources import secret_cipher, temporal_database
from opsmesh.bootstrap.temporal import connect_temporal
from opsmesh.config import Settings
from opsmesh.governance.audit.activities import AuditActivities
from opsmesh.governance.audit.workflow import AuditVerificationWorkflow
from opsmesh.platform.mail.activities import MailActivities
from opsmesh.platform.mail.service import MailAuthorization
from opsmesh.platform.mail.workflow import MailSendWorkflow
from opsmesh.shared.security.secrets import SecretCipher
from opsmesh.telemetry.context import restore_trace_context
from opsmesh.temporal.dispatch.dispatcher import TemporalOutboxDispatcher
from opsmesh.temporal.dispatch.outbox import TemporalOutboxPublisher
from opsmesh.workspaces.model_providers.activities import ModelProviderActivities
from opsmesh.workspaces.model_providers.workflow import ModelProviderProbeWorkflow


@dataclass(frozen=True)
class DispatchWorker:
    """The S4 worker runs the dispatch consumer and the feature Workflows."""

    dispatcher: TemporalOutboxDispatcher
    worker: Worker


@dataclass(frozen=True)
class FeatureRegistrations:
    """The feature Workflows and Activities one step registers on the Worker.

    The registration set is a fact of its own: a Workflow a service stages
    must be registered here or its durable execution never runs, so the
    set is assembled as data the assembly and the tests both read.
    """

    workflows: list[type] = field(default_factory=list)
    activities: list[Callable[..., Any]] = field(default_factory=list)


def feature_registrations(
    sessions: async_sessionmaker[AsyncSession],
    *,
    cipher: SecretCipher,
    mail_authorizations: dict[str, MailAuthorization],
) -> FeatureRegistrations:
    """Assemble the Workflows and Activities of every delivered step."""
    audit_activities = AuditActivities(sessions)
    mail_activities = MailActivities(cipher, sessions, mail_authorizations)
    model_provider_activities = ModelProviderActivities(cipher, sessions)
    return FeatureRegistrations(
        workflows=[
            AuditVerificationWorkflow,
            MailSendWorkflow,
            ModelProviderProbeWorkflow,
        ],
        activities=[
            audit_activities.verify_audit_segment,
            audit_activities.record_audit_verification_progress,
            audit_activities.complete_audit_verification,
            mail_activities.deliver_mail_intent,
            mail_activities.record_mail_cancellation,
            model_provider_activities.probe_connection,
        ],
    )


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
    """Build the Worker over the registration set of every delivered step."""
    registrations = feature_registrations(
        sessions, cipher=cipher, mail_authorizations=mail_source_authorizations()
    )
    return Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=registrations.workflows,
        activities=registrations.activities,
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
