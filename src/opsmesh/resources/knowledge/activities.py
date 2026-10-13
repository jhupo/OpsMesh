from contextlib import closing
from typing import TYPE_CHECKING

from sqlalchemy import select
from temporalio import activity

from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.resources.knowledge.content import (
    KnowledgeContentError,
    read_knowledge_content,
    resolve_knowledge_content,
)
from opsmesh.resources.knowledge.ingestion import KnowledgeSourceIngestionService
from opsmesh.resources.knowledge.models import KnowledgeSource, KnowledgeSourceIngestion
from opsmesh.resources.storage.backends import create_storage
from opsmesh.runtime.temporal.activities import (
    ActivityDependencies,
    authorize_execution,
    parameter_int,
)
from opsmesh.runtime.temporal.contracts import ExecutionRequest

if TYPE_CHECKING:
    from opsmesh.resources.knowledge.content import KnowledgeURLFetcher


class KnowledgeIngestionActivities:
    def __init__(self, services: ActivityDependencies, url_fetcher: "KnowledgeURLFetcher") -> None:
        self._services = services
        self._url_fetcher = url_fetcher

    @activity.defn(name="opsmesh.knowledge.ingest")
    def ingest(self, request: ExecutionRequest) -> None:
        version = parameter_int(request, "source_version", minimum=1)
        with self._services.transaction() as session:
            authorize_execution(session, request, action=WorkspaceAction.WRITE)
            service = KnowledgeSourceIngestionService(session)
            ingestion = service.start(
                workspace_id=request.workspace_id,
                source_id=request.resource_id,
                source_version=version,
            )
            if ingestion is None:
                return
            try:
                source = session.scalar(
                    select(KnowledgeSource).where(
                        KnowledgeSource.workspace_id == request.workspace_id,
                        KnowledgeSource.id == request.resource_id,
                    )
                )
                if source is None:
                    raise ValueError("Knowledge source is unavailable")
                plan = resolve_knowledge_content(session, source=source)
            except KnowledgeContentError as exc:
                service.fail(ingestion, exc.code)
                return
            ingestion_id = ingestion.id

        try:
            # URL/storage transfer uses the immutable admission plan with no
            # database connection or source row lock held during external I/O.
            with closing(create_storage(self._services.settings)) as storage:
                content = read_knowledge_content(
                    plan,
                    storage=storage,
                    url_fetcher=self._url_fetcher,
                )
            with self._services.transaction() as session:
                authorize_execution(session, request, action=WorkspaceAction.WRITE)
                current = session.scalar(
                    select(KnowledgeSourceIngestion).where(
                        KnowledgeSourceIngestion.workspace_id == request.workspace_id,
                        KnowledgeSourceIngestion.id == ingestion_id,
                    )
                )
                if current is None:
                    raise ValueError("Knowledge ingestion is unavailable")
                KnowledgeSourceIngestionService(session).complete(
                    ingestion=current, content=content, plan=plan
                )
        except Exception as exc:
            with self._services.transaction() as session:
                current = session.scalar(
                    select(KnowledgeSourceIngestion).where(
                        KnowledgeSourceIngestion.workspace_id == request.workspace_id,
                        KnowledgeSourceIngestion.id == ingestion_id,
                    )
                )
                if current is not None and current.status != "succeeded":
                    KnowledgeSourceIngestionService(session).fail(
                        current,
                        exc.code if isinstance(exc, KnowledgeContentError) else "ingestion_failed",
                    )
            if not isinstance(exc, KnowledgeContentError):
                raise
