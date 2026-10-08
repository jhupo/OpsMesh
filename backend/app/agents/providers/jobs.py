import asyncio

from backend.app.agents.providers.contracts import provider_health_probes
from backend.app.agents.providers.health import ModelProviderHealthService
from backend.app.runtime.queues.context import WorkerJobHandlerContext
from backend.app.runtime.queues.contracts import JobPayload
from backend.app.runtime.workers.routing import positive_float


class ModelProviderHealthJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        if job.requested_by_user_id is None:
            raise ValueError("Model provider health check jobs require requested_by_user_id")
        asyncio.run(
            ModelProviderHealthService(
                self._context.session,
                self._context.secret_service(context="model provider health check"),
            ).run_health_check(
                workspace_id=job.workspace_id,
                credential_id=job.resource_id,
                actor_user_id=job.requested_by_user_id,
                probes=provider_health_probes(job.routing.get("probes")),
                timeout_seconds=positive_float(
                    job.routing.get("timeout_seconds"),
                    default=15,
                    key="timeout_seconds",
                    context="Model provider health check job",
                    maximum=60,
                ),
            )
        )
