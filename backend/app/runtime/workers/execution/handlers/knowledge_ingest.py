from backend.app.domains.knowledge.ingestion import KnowledgeSourceIngestionService
from backend.app.domains.workspace.storage.storage import create_storage
from backend.app.runtime.workers.contracts import JobPayload
from backend.app.runtime.workers.execution.handlers.context import WorkerJobHandlerContext
from backend.app.runtime.workers.execution.routing import required_int


class KnowledgeIngestJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        source_version = required_int(
            job.routing,
            "source_version",
            context="Knowledge ingest job",
            minimum=1,
        )
        service = KnowledgeSourceIngestionService(self._context.session)
        ingestion = service.start(
            workspace_id=job.workspace_id,
            source_id=job.resource_id,
            source_version=source_version,
        )
        self._context.session.commit()
        if ingestion is None:
            return
        try:
            settings = self._context.require_settings(context="knowledge ingestion")
            service.process(
                ingestion=ingestion,
                storage=create_storage(settings),
            )
            self._context.session.commit()
        except Exception:
            service.fail(ingestion, "ingestion_failed")
            self._context.session.commit()
            raise

