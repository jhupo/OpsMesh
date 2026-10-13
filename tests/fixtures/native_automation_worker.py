"""Native message, authored-node, Agent and reply workflows on real Temporal/PG."""

import asyncio
import json
from concurrent.futures import Future
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Thread
from types import SimpleNamespace

import httpx
from sqlalchemy.orm import sessionmaker
from temporalio import activity

from opsmesh.agents.execution.providers.openai.activities import AgentExecutionActivities
from opsmesh.messaging.channels.delivery import ChannelDeliveryService
from opsmesh.orchestration.automations.activities import AutomationActivities
from opsmesh.orchestration.automations.workflows import (
    AutomationEventWorkflow,
    ReplyDeliveryWorkflow,
)
from opsmesh.orchestration.definitions.activities import OrchestrationNodeActivities
from opsmesh.orchestration.definitions.workflow import OrchestrationNodeWorkflow
from opsmesh.orchestration.scheduling.activities import ScheduleActivities
from opsmesh.orchestration.scheduling.workflows import ScheduleControlWorkflow, ScheduleTickWorkflow
from opsmesh.orchestration.webhooks.delivery import WebhookDeliveryService
from opsmesh.resources.memory.activities import MemoryActivities
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionRequest
from opsmesh.runtime.temporal.workflows import (
    MemoryEmbeddingWorkflow,
    MemoryIndexWorkflow,
    WebhookDeliveryWorkflow,
)
from opsmesh.teams.execution.activities import TeamOrchestrationActivities
from opsmesh.teams.execution.workflow import TeamOrchestrationWorkflow
from tests.fixtures.native_agent_worker import native_agent_worker


class NativeAutomationDriver:
    def __init__(self, host, loop):
        self.host = host
        self.loop = loop

    def until(self, predicate, *, timeout=60):
        async def dispatch():
            async with asyncio.timeout(timeout):
                while True:
                    await self.host.publisher().publish_pending()
                    result = await asyncio.to_thread(predicate)
                    if result:
                        return result
                    await asyncio.sleep(0.05)

        return asyncio.run_coroutine_threadsafe(dispatch(), self.loop).result(timeout + 5)


class OfflineWebhookActivities:
    def __init__(self, services, transport):
        self.services = services
        self.transport = transport

    @activity.defn(name="opsmesh.webhook.deliver")
    def deliver(self, request: ExecutionRequest) -> float:
        with self.services.transaction() as session:
            attempt = WebhookDeliveryService(
                session, self.services.secrets(), self.transport
            ).deliver(workspace_id=request.workspace_id, delivery_attempt_id=request.resource_id)
            if attempt.status in {"succeeded", "dead_lettered"}:
                return -1
            assert attempt.next_retry_at is not None
            return max(0, (attempt.next_retry_at - datetime.now(UTC)).total_seconds())

    @activity.defn(name="opsmesh.channel.send")
    def send_channel(self, request: ExecutionRequest) -> str:
        def receive(outgoing):
            payload = json.loads(outgoing.content)
            if hasattr(self.transport, "calls"):
                self.transport.calls.append(
                    {"body": outgoing.content, "headers": dict(outgoing.headers)}
                )
            return httpx.Response(
                200,
                json={
                    "delivery_id": payload["delivery_id"],
                    "status": "delivered",
                    "provider_message_id": "offline-provider",
                },
            )

        with self.services.transaction() as session:
            return ChannelDeliveryService(
                session, self.services.secrets(), httpx.MockTransport(receive)
            ).send(request.workspace_id, request.resource_id)

    @activity.defn(name="opsmesh.channel.interrupted")
    def interrupted(self, request: ExecutionRequest) -> str:
        with self.services.transaction() as session:
            return ChannelDeliveryService(session, self.services.secrets()).interrupted(
                request.workspace_id, request.resource_id
            )


@contextmanager
def native_automation_worker(
    session,
    settings,
    provider,
    *,
    transport,
    stream=False,
    additional_workflows=(),
    additional_activities=(),
):
    sessions = sessionmaker(session.get_bind(), expire_on_commit=False)
    state = SimpleNamespace(
        sessions=sessions, activities=AgentExecutionActivities(sessions, settings)
    )
    ready = Future()
    finished = Future()

    async def execute():
        services = ActivityDependencies(sessions, settings)
        nodes = OrchestrationNodeActivities(services)
        teams = TeamOrchestrationActivities(services)
        automation = AutomationActivities(services)
        memory = MemoryActivities(services)
        webhooks = OfflineWebhookActivities(services, transport)
        async with native_agent_worker(state, provider, stream=stream) as host:
            schedule_services = ActivityDependencies(
                sessions, settings.model_copy(update={"temporal_task_queue": host.task_queue})
            )
            schedules = ScheduleActivities(schedule_services, host.environment.client)
            async with host.worker(
                additional_workflows=[
                    AutomationEventWorkflow,
                    ReplyDeliveryWorkflow,
                    OrchestrationNodeWorkflow,
                    TeamOrchestrationWorkflow,
                    ScheduleControlWorkflow,
                    ScheduleTickWorkflow,
                    WebhookDeliveryWorkflow,
                    MemoryIndexWorkflow,
                    MemoryEmbeddingWorkflow,
                    *additional_workflows,
                ],
                additional_activities=[
                    automation.dispatch,
                    automation.prepare_reply,
                    automation.settle_reply,
                    nodes.prepare,
                    nodes.complete,
                    nodes.fail,
                    nodes.cancel,
                    teams.advance,
                    teams.record_unknown,
                    schedules.synchronize,
                    schedules.materialize,
                    webhooks.deliver,
                    webhooks.send_channel,
                    webhooks.interrupted,
                    memory.index,
                    memory.embed,
                    *additional_activities,
                ],
            ):
                stopped = asyncio.Event()
                ready.set_result(
                    (NativeAutomationDriver(host, asyncio.get_running_loop()), stopped)
                )
                await stopped.wait()

    def run():
        try:
            asyncio.run(execute())
        except BaseException as exc:
            finished.set_exception(exc)
            if not ready.done():
                ready.set_exception(exc)
        else:
            finished.set_result(None)

    thread = Thread(target=run, name="native-automation-validation")
    thread.start()
    try:
        driver, stopped = ready.result(timeout=60)
        yield driver
    finally:
        if ready.done() and ready.exception() is None:
            driver, stopped = ready.result()
            driver.loop.call_soon_threadsafe(stopped.set)
        thread.join(timeout=60)
        assert not thread.is_alive(), "Native automation Worker did not shut down"
        finished.result()
