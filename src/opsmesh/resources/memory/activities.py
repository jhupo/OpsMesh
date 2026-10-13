from contextlib import ExitStack

from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from opsmesh.governance.costs.service import CostBudgetExceededError
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.resources.memory.embedding_service import (
    MemoryEmbeddingError,
    WorkspaceMemoryEmbeddingProviderResolver,
    WorkspaceMemoryEmbeddingService,
)
from opsmesh.resources.memory.indexing import WorkspaceMemoryIndexingService
from opsmesh.resources.memory.models import WorkspaceMemoryConfiguration
from opsmesh.runtime.temporal.activities import (
    ActivityDependencies,
    authorize_execution,
    parameter_int,
)
from opsmesh.runtime.temporal.contracts import ExecutionRequest


class MemoryActivities:
    def __init__(self, services: ActivityDependencies) -> None:
        self._services = services

    @activity.defn(name="opsmesh.memory.index")
    def index(self, request: ExecutionRequest) -> None:
        with self._services.transaction() as session:
            authorize_execution(
                session, request, action=WorkspaceAction.WRITE, service_operation=True
            )
            service = WorkspaceMemoryIndexingService(session)
            match request.parameters.get("source_type"):
                case "task":
                    service.refresh_task(
                        workspace_id=request.workspace_id, task_id=request.resource_id
                    )
                case "workspace_file":
                    service.refresh_file(
                        workspace_id=request.workspace_id, file_id=request.resource_id
                    )
                case "artifact":
                    service.refresh_artifact(
                        workspace_id=request.workspace_id, artifact_id=request.resource_id
                    )
                case _:
                    raise ValueError("Unsupported memory index source")

    @activity.defn(name="opsmesh.memory.embed")
    def embed(self, request: ExecutionRequest) -> None:
        generation = parameter_int(request, "embedding_generation")
        with self._services.transaction() as session:
            authorize_execution(
                session, request, action=WorkspaceAction.WRITE, service_operation=True
            )
            service = WorkspaceMemoryEmbeddingService(session)
            work = service.prepare(
                workspace_id=request.workspace_id,
                memory_entry_id=request.resource_id,
                embedding_generation=generation,
            )
            if work is None:
                return

        try:
            with ExitStack() as resources:
                with self._services.transaction() as session:
                    authorize_execution(
                        session, request, action=WorkspaceAction.WRITE, service_operation=True
                    )
                    configuration = session.scalar(
                        select(WorkspaceMemoryConfiguration).where(
                            WorkspaceMemoryConfiguration.workspace_id == request.workspace_id,
                        )
                    )
                    if (
                        configuration is None
                        or not configuration.embedding_enabled
                        or configuration.embedding_credential_id != work.credential_id
                        or configuration.embedding_model != work.model
                        or configuration.embedding_dimensions != work.dimensions
                    ):
                        raise MemoryEmbeddingError(
                            "memory_configuration_stale", "Memory configuration changed"
                        )
                    provider = WorkspaceMemoryEmbeddingProviderResolver(
                        session, self._services.secrets()
                    ).resolve(
                        workspace_id=request.workspace_id,
                        configuration=configuration,
                        max_retries=0,
                    )
                    resources.callback(provider.close)
                # Release the connection before network I/O. The native client's
                # secret and transport remain inside this Activity's cleanup scope.
                result = provider.embed(work.text)
            with self._services.transaction() as session:
                authorize_execution(
                    session, request, action=WorkspaceAction.WRITE, service_operation=True
                )
                WorkspaceMemoryEmbeddingService(session).complete(work, result)
        except Exception as exc:
            code = (
                "embedding_cost_budget_exceeded"
                if isinstance(exc, CostBudgetExceededError)
                else exc.code
                if isinstance(exc, MemoryEmbeddingError)
                else "embedding_provider_failed"
            )
            with self._services.transaction() as session:
                WorkspaceMemoryEmbeddingService(session).fail(work, code)
            raise ApplicationError(
                "Memory embedding failed",
                type=code,
                non_retryable=code
                not in {
                    "embedding_provider_timeout",
                    "embedding_provider_unavailable",
                    "embedding_provider_rate_limited",
                    "embedding_provider_failed",
                },
            ) from None
