"""Governed source operations; indexing is a durable job, never a model tool loop."""

from uuid import UUID

from sqlalchemy import and_, false, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from opsmesh.capabilities.tools.contracts import ToolContext, ToolResourceNotFoundError
from opsmesh.capabilities.tools.events import ProductToolEventRecorder
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.resources.files.models import WorkspaceFile
from opsmesh.resources.files.runtime_policy import runtime_file_denial_code
from opsmesh.resources.knowledge.contracts import (
    KnowledgeSourceCreate,
    KnowledgeSourceType,
    KnowledgeSourceUpdate,
)
from opsmesh.resources.knowledge.ingestion import KnowledgeSourceIngestionService
from opsmesh.resources.knowledge.models import KnowledgeSource
from opsmesh.resources.knowledge.service import KnowledgeSourceService
from opsmesh.resources.memory.authorization import AuthorizedMemoryScope
from opsmesh.resources.memory.models import WorkspaceMemoryEntry
from opsmesh.shared.db.pagination import page_scalars_by_offset


class KnowledgeProductTools:
    def __init__(self, session: Session, events: ProductToolEventRecorder) -> None:
        self.session = session
        self.events = events

    def list_knowledge(
        self,
        context: ToolContext,
        *,
        scopes: tuple[AuthorizedMemoryScope, ...],
        limit: int = 20,
        offset: int = 0,
        include_archived: bool = False,
    ) -> dict[str, object]:
        context.require_tool("list_knowledge")
        if not 1 <= limit <= 50 or offset < 0:
            raise ValueError("Invalid knowledge page")
        statement = select(KnowledgeSource).where(
            KnowledgeSource.workspace_id == context.workspace_id,
            self._scope_predicate(context, scopes),
        )
        if not include_archived:
            statement = statement.where(KnowledgeSource.status != "archived")
        sources, total = page_scalars_by_offset(
            self.session,
            statement.order_by(KnowledgeSource.updated_at.desc(), KnowledgeSource.id),
            limit=limit,
            offset=offset,
        )
        self.events.append(context, "tool.completed", "list_knowledge")
        return {"items": [source_payload(source) for source in sources], "total": total}

    def get_knowledge(
        self,
        context: ToolContext,
        *,
        source_id: UUID,
        scopes: tuple[AuthorizedMemoryScope, ...],
        content_offset: int = 0,
        content_limit: int = 8000,
    ) -> dict[str, object]:
        context.require_tool("get_knowledge")
        if content_offset < 0 or not 1 <= content_limit <= 12000:
            raise ValueError("Invalid knowledge content page")
        source = self._source(context, source_id, scopes)
        result = source_payload(source)
        if source.source_type == "text":
            text = str(source.source_config["text"])
            result.update(
                {
                    "content": text[content_offset : content_offset + content_limit],
                    "content_offset": content_offset,
                    "content_length": len(text),
                    "has_more": content_offset + content_limit < len(text),
                }
            )
        self.events.append(context, "tool.completed", "get_knowledge")
        return result

    def create_knowledge(
        self,
        context: ToolContext,
        *,
        name: str,
        description: str,
        content: str | None,
        workspace_file_id: UUID | None,
        scopes: tuple[AuthorizedMemoryScope, ...],
        allowed_file_ids: set[UUID],
    ) -> dict[str, object]:
        context.require_tool("create_knowledge")
        user = self._user(context)
        source_type: KnowledgeSourceType = "text" if content is not None else "workspace_file"
        self._require_write(context, scopes, source_type)
        if (content is None) == (workspace_file_id is None):
            raise ValueError("Provide exactly one of content or workspace_file_id")
        if workspace_file_id is not None:
            self._require_file(context, workspace_file_id, allowed_file_ids)
        source = KnowledgeSourceService(self.session).create(
            workspace_id=context.workspace_id,
            actor_user_id=user.user_id,
            command=KnowledgeSourceCreate(
                name=name,
                description=description,
                source_type=source_type,
                uri=None,
                workspace_file_id=workspace_file_id,
                config={"text": content} if content is not None else {},
            ),
        )
        result = self._request_ingestion(context, source)
        self.events.append(context, "tool.completed", "create_knowledge")
        return result

    def update_knowledge(
        self,
        context: ToolContext,
        *,
        source_id: UUID,
        expected_version: int,
        name: str | None,
        description: str | None,
        content: str | None,
        workspace_file_id: UUID | None,
        scopes: tuple[AuthorizedMemoryScope, ...],
        allowed_file_ids: set[UUID],
    ) -> dict[str, object]:
        context.require_tool("update_knowledge")
        source = self._source(context, source_id, scopes, write=True)
        user = self._user(context)
        if content is not None and workspace_file_id is not None:
            raise ValueError("Content and workspace_file_id are mutually exclusive")
        if all(value is None for value in (name, description, content, workspace_file_id)):
            raise ValueError("At least one knowledge field is required")
        if source.source_type == "url":
            raise ValueError("Agent knowledge updates support text and authorized files")
        if workspace_file_id is not None:
            self._require_file(context, workspace_file_id, allowed_file_ids)
        target_type: KnowledgeSourceType = "text" if content is not None else "workspace_file"
        source_type = target_type if content is not None or workspace_file_id is not None else None
        self._require_write(context, scopes, source_type or source.source_type)
        updated = KnowledgeSourceService(self.session).update(
            workspace_id=context.workspace_id,
            source_id=source.id,
            actor_user_id=user.user_id,
            command=KnowledgeSourceUpdate(
                expected_version=expected_version,
                name=name,
                description=description,
                source_type=source_type,
                uri=None,
                workspace_file_id=workspace_file_id,
                config={"text": content}
                if content is not None
                else ({} if workspace_file_id is not None else None),
                status=None,
            ),
        )
        if updated is None:
            raise ToolResourceNotFoundError("Knowledge source unavailable")
        result = self._request_ingestion(context, updated)
        self.events.append(context, "tool.completed", "update_knowledge")
        return result

    def archive_knowledge(
        self,
        context: ToolContext,
        *,
        source_id: UUID,
        expected_version: int,
        scopes: tuple[AuthorizedMemoryScope, ...],
    ) -> dict[str, object]:
        context.require_tool("archive_knowledge")
        source = self._source(context, source_id, scopes, write=True)
        user = self._user(context)
        ResourceAuthorizationService(self.session, user).require(
            context.workspace_id,
            ResourceKind.KNOWLEDGE,
            source.id,
            ResourceAction.DELETE,
        )
        archived = KnowledgeSourceService(self.session).set_status(
            workspace_id=context.workspace_id,
            source_id=source.id,
            actor_user_id=user.user_id,
            expected_version=expected_version,
            status="archived",
        )
        if archived is None:
            raise ToolResourceNotFoundError("Knowledge source unavailable")
        self.events.append(context, "tool.completed", "archive_knowledge")
        return source_payload(archived)

    def get_knowledge_ingestion(
        self,
        context: ToolContext,
        *,
        source_id: UUID,
        ingestion_id: UUID,
        scopes: tuple[AuthorizedMemoryScope, ...],
    ) -> dict[str, object]:
        context.require_tool("get_knowledge_ingestion")
        source = self._source(context, source_id, scopes)
        ingestion = KnowledgeSourceIngestionService(self.session).get_ingestion(
            workspace_id=context.workspace_id,
            source_id=source.id,
            ingestion_id=ingestion_id,
        )
        if ingestion is None:
            raise ToolResourceNotFoundError("Knowledge ingestion unavailable")
        embedding_counts = {
            status: count
            for status, count in self.session.execute(
                select(WorkspaceMemoryEntry.embedding_status, func.count())
                .where(
                    WorkspaceMemoryEntry.workspace_id == context.workspace_id,
                    WorkspaceMemoryEntry.source_type == "knowledge_source",
                    WorkspaceMemoryEntry.source_id == str(source.id),
                    WorkspaceMemoryEntry.status == "active",
                    WorkspaceMemoryEntry.memory_metadata["source_version"].as_integer()
                    == ingestion.source_version,
                )
                .group_by(WorkspaceMemoryEntry.embedding_status)
            ).all()
        }
        self.events.append(context, "tool.completed", "get_knowledge_ingestion")
        return {
            "ingestion_id": str(ingestion.id),
            "source_id": str(source.id),
            "source_version": ingestion.source_version,
            "current_version": source.version,
            "status": ingestion.status,
            "chunk_count": ingestion.chunk_count,
            "error_code": ingestion.error_code,
            "embedding_counts": embedding_counts,
            "vector_ready": (
                ingestion.status == "succeeded"
                and source.status == "active"
                and ingestion.source_version == source.version
                and ingestion.chunk_count > 0
                and embedding_counts.get("ready", 0) == ingestion.chunk_count
            ),
        }

    def _user(self, context: ToolContext) -> AuthenticatedUser:
        if context.agent_run_id is None:
            raise ValueError("Knowledge tools require an authorized agent run")
        return ExecutionIdentityService(self.session).for_run(
            context.workspace_id,
            context.agent_run_id,
        )

    def _scope_predicate(
        self,
        context: ToolContext,
        scopes: tuple[AuthorizedMemoryScope, ...],
    ) -> ColumnElement[bool]:
        source_types = [
            kind
            for kind in ("text", "workspace_file", "url")
            if any(
                scope.allows(
                    source_type="knowledge_source",
                    tags={"knowledge_source", kind},
                    scope_type="workspace",
                    scope_id=str(context.workspace_id),
                )
                for scope in scopes
            )
        ]
        return and_(
            KnowledgeSource.source_type.in_(source_types) if source_types else false(),
            ResourceAuthorizationService(self.session, self._user(context)).predicate(
                context.workspace_id,
                ResourceKind.KNOWLEDGE,
                KnowledgeSource.id.__clause_element__(),
            ),
        )

    def _source(
        self,
        context: ToolContext,
        source_id: UUID,
        scopes: tuple[AuthorizedMemoryScope, ...],
        *,
        write: bool = False,
    ) -> KnowledgeSource:
        statement = select(KnowledgeSource).where(
            KnowledgeSource.workspace_id == context.workspace_id,
            KnowledgeSource.id == source_id,
            self._scope_predicate(context, scopes),
        )
        if write:
            statement = statement.with_for_update()
        source = self.session.scalar(statement)
        if source is None:
            raise ToolResourceNotFoundError("Knowledge source not found in authorized scope")
        if write:
            self._require_write(context, scopes, source.source_type)
            ResourceAuthorizationService(self.session, self._user(context)).require(
                context.workspace_id,
                ResourceKind.KNOWLEDGE,
                source.id,
                ResourceAction.UPDATE,
            )
        return source

    @staticmethod
    def _require_write(
        context: ToolContext,
        scopes: tuple[AuthorizedMemoryScope, ...],
        kind: str,
    ) -> None:
        if not any(
            scope.allows_write(
                source_type="knowledge_source",
                tags={"knowledge_source", kind},
                scope_type="workspace",
                scope_id=str(context.workspace_id),
            )
            for scope in scopes
        ):
            raise ValueError("Knowledge write is outside the authorized resource scope")

    def _require_file(self, context: ToolContext, file_id: UUID, allowed_ids: set[UUID]) -> None:
        file = self.session.scalar(
            select(WorkspaceFile).where(
                WorkspaceFile.workspace_id == context.workspace_id,
                WorkspaceFile.id == file_id,
                WorkspaceFile.status == "active",
                WorkspaceFile.id.in_(allowed_ids),
            )
        )
        if file is None or runtime_file_denial_code(file) is not None:
            raise ToolResourceNotFoundError("Knowledge file not found in authorized scope")

    def _request_ingestion(
        self, context: ToolContext, source: KnowledgeSource
    ) -> dict[str, object]:
        ingestion = KnowledgeSourceIngestionService(self.session).request(
            workspace_id=context.workspace_id,
            source_id=source.id,
            requested_by_user_id=self._user(context).user_id,
        )
        return source_payload(source) | {
            "ingestion_id": str(ingestion.id),
            "ingestion_status": ingestion.status,
        }


def source_payload(source: KnowledgeSource) -> dict[str, object]:
    return {
        "source_id": str(source.id),
        "name": source.name,
        "description": source.description,
        "source_type": source.source_type,
        "workspace_file_id": (
            str(source.workspace_file_id) if source.workspace_file_id is not None else None
        ),
        "version": source.version,
        "status": source.status,
    }
