import asyncio

from backend.app.audit.integrity import AuditIntegrityService
from backend.app.model_providers.health_probes import provider_health_probes
from backend.app.model_providers.health_service import ModelProviderHealthService
from backend.app.secrets.rotation import HostedSecretReencryptService
from backend.app.workers.job_handlers.context import WorkerJobHandlerContext
from backend.app.workers.job_routing import positive_float
from backend.app.workers.jobs import JobPayload


class SecretReencryptJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        scope = job.routing.get("scope")
        workspace_id = None if scope == "global" else job.workspace_id
        HostedSecretReencryptService(
            self._context.session,
            self._context.secret_service(context="secret reencryption"),
        ).reencrypt(workspace_id=workspace_id)
        self._context.session.commit()


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


class AuditIntegrityJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        if job.resource_id != job.workspace_id:
            raise ValueError("Audit integrity job workspace mismatch")
        AuditIntegrityService(self._context.session).check_workspace(job.workspace_id)
