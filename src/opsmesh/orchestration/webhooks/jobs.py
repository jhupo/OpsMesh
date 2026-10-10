from opsmesh.orchestration.webhooks.delivery import WebhookDeliveryService
from opsmesh.runtime.queues.context import WorkerJobHandlerContext
from opsmesh.runtime.queues.contracts import JobPayload


class WebhookDeliveryJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        WebhookDeliveryService(
            self._context.session,
            self._context.secret_service(context="webhook delivery"),
        ).deliver(
            workspace_id=job.workspace_id,
            delivery_attempt_id=job.resource_id,
        )
