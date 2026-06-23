from backend.app.memory.indexing import WorkspaceMemoryIndexingService
from backend.app.workers.job_handlers.context import WorkerJobHandlerContext
from backend.app.workers.job_routing import required_string
from backend.app.workers.jobs import JobPayload


class MemoryIndexJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        source_type = required_string(
            job.routing,
            "source_type",
            context="Memory index job",
        )
        service = WorkspaceMemoryIndexingService(self._context.session)
        match source_type:
            case "task":
                service.refresh_task(workspace_id=job.workspace_id, task_id=job.resource_id)
            case "workspace_file":
                service.refresh_file(workspace_id=job.workspace_id, file_id=job.resource_id)
            case "artifact":
                service.refresh_artifact(workspace_id=job.workspace_id, artifact_id=job.resource_id)
            case _:
                raise ValueError(f"Unsupported memory index source_type: {source_type}")
        self._context.session.commit()
