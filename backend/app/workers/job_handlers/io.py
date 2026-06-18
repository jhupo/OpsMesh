from backend.app.api.services.exports import WorkspaceExportService
from backend.app.files.storage import create_storage
from backend.app.webhooks.service import WebhookDeliveryService
from backend.app.workers.job_handlers.context import WorkerJobHandlerContext
from backend.app.workers.jobs import JobPayload


class WorkspaceArchiveExportJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        if self._context.settings is None:
            error = "Worker settings are required for workspace archive export"
            WorkspaceExportService(self._context.session).fail_archive_export_job(
                job=job,
                error=error,
            )
            raise ValueError(error)
        service = WorkspaceExportService(self._context.session)
        service.run_archive_export_job(job=job, storage=create_storage(self._context.settings))


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
