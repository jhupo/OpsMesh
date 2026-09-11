from sqlalchemy import select

from backend.app.memory.embeddings import (
    MemoryEmbeddingError,
    MemoryEmbeddingWork,
    WorkspaceMemoryEmbeddingProviderResolver,
    WorkspaceMemoryEmbeddingService,
)
from backend.app.memory.models import WorkspaceMemoryConfiguration
from backend.app.observability.cost_service import CostBudgetExceededError
from backend.app.workers.job_handlers.context import WorkerJobHandlerContext
from backend.app.workers.jobs import JobPayload


class MemoryEmbeddingJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        embedding_generation = _nonnegative_int(
            job.routing.get("embedding_generation"),
            key="embedding_generation",
        )
        service = WorkspaceMemoryEmbeddingService(self._context.session)
        work = service.prepare(
            workspace_id=job.workspace_id,
            memory_entry_id=job.resource_id,
            embedding_generation=embedding_generation,
        )
        self._context.session.commit()
        if work is None:
            return
        try:
            configuration = self._context.session.scalar(
                select(WorkspaceMemoryConfiguration).where(
                    WorkspaceMemoryConfiguration.workspace_id == job.workspace_id,
                    WorkspaceMemoryConfiguration.version == work.configuration_version,
                )
            )
            if configuration is None:
                raise MemoryEmbeddingError(
                    "memory_configuration_stale",
                    "Workspace memory configuration changed",
                )
            provider = WorkspaceMemoryEmbeddingProviderResolver(
                self._context.session,
                self._context.secret_service(context="memory embedding"),
            ).resolve(
                workspace_id=job.workspace_id,
                configuration=configuration,
            )
            result = provider.embed(work.text)
            service.complete(work, result)
            self._context.session.commit()
        except CostBudgetExceededError as exc:
            self._fail(service, work, "embedding_cost_budget_exceeded")
            raise MemoryEmbeddingError(
                "embedding_cost_budget_exceeded",
                "Workspace model cost budget blocks memory embedding",
            ) from exc
        except MemoryEmbeddingError as exc:
            self._fail(service, work, exc.code)
            raise
        except Exception as exc:
            self._fail(service, work, "embedding_provider_failed")
            raise MemoryEmbeddingError(
                "embedding_provider_failed",
                "Memory embedding failed",
            ) from exc

    def _fail(
        self,
        service: WorkspaceMemoryEmbeddingService,
        work: MemoryEmbeddingWork,
        error_code: str,
    ) -> None:
        service.fail(work, error_code)
        self._context.session.commit()


def _nonnegative_int(value: object, *, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Memory embedding job {key} must be a non-negative integer")
    return value
