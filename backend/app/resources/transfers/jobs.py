from backend.app.resources.storage.storage import create_storage
from backend.app.resources.transfers.service import WorkspaceExportService
from backend.app.runtime.queues.context import WorkerJobHandlerContext
from backend.app.runtime.queues.contracts import JobPayload


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
