from datetime import UTC, datetime

from temporalio import activity

from opsmesh.orchestration.webhooks.delivery import WebhookDeliveryService
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionRequest


class WebhookDeliveryActivities:
    def __init__(self, services: ActivityDependencies) -> None:
        self._services = services

    @activity.defn(name="opsmesh.webhook.deliver")
    def deliver(self, request: ExecutionRequest) -> float:
        with self._services.transaction() as session:
            attempt = WebhookDeliveryService(session, self._services.secrets()).deliver(
                workspace_id=request.workspace_id, delivery_attempt_id=request.resource_id
            )
            if attempt.status in {"succeeded", "dead_lettered"}:
                return -1
            if attempt.next_retry_at is None:
                raise ValueError("Webhook failed without a durable retry deadline")
            return max(0, (attempt.next_retry_at - datetime.now(UTC)).total_seconds())
