
from opsmesh.governance.audit.integrity import AuditIntegrityService
from opsmesh.runtime.queues.context import WorkerJobHandlerContext
from opsmesh.runtime.queues.contracts import JobPayload


class AuditIntegrityJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        if job.resource_id != job.workspace_id:
            raise ValueError("Audit integrity job workspace mismatch")
        AuditIntegrityService(self._context.session).check_workspace(job.workspace_id)
