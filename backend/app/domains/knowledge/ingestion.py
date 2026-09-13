from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.db.errors import flush_or_raise_conflict
from backend.app.core.db.pagination import page_scalars_by_offset
from backend.app.domains.agents.memory.configuration import initial_embedding_status
from backend.app.domains.agents.memory.indexing import chunk_text
from backend.app.domains.agents.memory.models import (
    WorkspaceMemoryEntry,
    memory_content_fingerprint,
)
from backend.app.domains.knowledge.models import KnowledgeSource, KnowledgeSourceIngestion
from backend.app.domains.workspace.storage.models import WorkspaceFile
from backend.app.domains.workspace.storage.storage import (
    ObjectStorage,
    StorageObjectReadError,
    StorageObjectTooLargeError,
)
from backend.app.observability.audit_service import AuditService
from backend.app.runtime.workers.contracts import JobPayload, JobType
from backend.app.runtime.workers.queue.redis import RedisQueue

MAX_SOURCE_BYTES = 10 * 1024 * 1024
SUPPORTED_TEXT_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
        "application/xml",
    }
)


@dataclass(frozen=True, slots=True)
class KnowledgeIngestionResult:
    ingestion_id: UUID
    source_id: UUID
    source_version: int
    status: str
    byte_count: int
    chunk_count: int
    error_code: str | None


class KnowledgeSourceIngestionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def request(
        self,
        *,
        workspace_id: UUID,
        source_id: UUID,
        requested_by_user_id: UUID,
    ) -> KnowledgeSourceIngestion:
        source = self._lock_source(workspace_id, source_id)
        if source is None:
            raise ValueError("Knowledge source not found")
        if source.status != "active":
            raise ValueError("Only active knowledge sources can be ingested")
        if source.source_type != "workspace_file":
            raise ValueError("URL ingestion requires an isolated fetch runtime")
        existing = self._session.scalar(
            select(KnowledgeSourceIngestion).where(
                KnowledgeSourceIngestion.workspace_id == workspace_id,
                KnowledgeSourceIngestion.source_id == source.id,
                KnowledgeSourceIngestion.source_version == source.version,
            )
        )
        if existing is not None:
            if existing.status == "failed":
                existing.status = "pending"
                existing.attempts = 0
                existing.error_code = None
                existing.started_at = None
                existing.completed_at = None
            else:
                return existing
        else:
            existing = KnowledgeSourceIngestion(
                workspace_id=workspace_id,
                requested_by_user_id=requested_by_user_id,
                source_id=source.id,
                source_version=source.version,
                status="pending",
                attempts=0,
                byte_count=0,
                chunk_count=0,
            )
            self._session.add(existing)
        flush_or_raise_conflict(
            self._session,
            "Knowledge source ingestion is already requested for this version",
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=requested_by_user_id,
            action="knowledge_source.ingestion_requested",
            target_type="knowledge_source",
            target_id=source.id,
            metadata={"source_version": source.version, "ingestion_id": str(existing.id)},
        )
        return existing

    def list_ingestions(
        self,
        *,
        workspace_id: UUID,
        source_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[KnowledgeSourceIngestion], int]:
        statement = select(KnowledgeSourceIngestion).where(
            KnowledgeSourceIngestion.workspace_id == workspace_id,
            KnowledgeSourceIngestion.source_id == source_id,
        )
        return page_scalars_by_offset(
            self._session,
            statement.order_by(
                KnowledgeSourceIngestion.created_at.desc(),
                KnowledgeSourceIngestion.id.desc(),
            ),
            limit=limit,
            offset=offset,
        )

    def get_ingestion(
        self,
        *,
        workspace_id: UUID,
        source_id: UUID,
        ingestion_id: UUID,
    ) -> KnowledgeSourceIngestion | None:
        return self._session.scalar(
            select(KnowledgeSourceIngestion).where(
                KnowledgeSourceIngestion.workspace_id == workspace_id,
                KnowledgeSourceIngestion.source_id == source_id,
                KnowledgeSourceIngestion.id == ingestion_id,
            )
        )

    def start(
        self,
        *,
        workspace_id: UUID,
        source_id: UUID,
        source_version: int,
    ) -> KnowledgeSourceIngestion | None:
        ingestion = self._session.scalar(
            select(KnowledgeSourceIngestion)
            .where(
                KnowledgeSourceIngestion.workspace_id == workspace_id,
                KnowledgeSourceIngestion.source_id == source_id,
                KnowledgeSourceIngestion.source_version == source_version,
            )
            .with_for_update()
        )
        if ingestion is None or ingestion.status == "succeeded":
            return None
        if ingestion.status == "processing":
            return None
        source = self._session.scalar(
            select(KnowledgeSource).where(
                KnowledgeSource.workspace_id == workspace_id,
                KnowledgeSource.id == source_id,
            )
        )
        if source is None or source.status != "active" or source.version != source_version:
            self.fail(ingestion, "source_version_stale")
            return None
        ingestion.status = "processing"
        ingestion.attempts += 1
        ingestion.started_at = datetime.now(UTC)
        ingestion.completed_at = None
        ingestion.error_code = None
        return ingestion

    def process(
        self,
        *,
        ingestion: KnowledgeSourceIngestion,
        storage: ObjectStorage,
    ) -> KnowledgeIngestionResult:
        source = self._session.scalar(
            select(KnowledgeSource).where(
                KnowledgeSource.workspace_id == ingestion.workspace_id,
                KnowledgeSource.id == ingestion.source_id,
                KnowledgeSource.version == ingestion.source_version,
                KnowledgeSource.status == "active",
            )
        )
        if source is None or source.workspace_file_id is None:
            return self._fail_result(ingestion, "source_version_stale")
        workspace_file = self._session.scalar(
            select(WorkspaceFile).where(
                WorkspaceFile.workspace_id == ingestion.workspace_id,
                WorkspaceFile.id == source.workspace_file_id,
                WorkspaceFile.status == "active",
            )
            )
        if workspace_file is None:
            return self._fail_result(ingestion, "workspace_file_not_found")
        content_type = workspace_file.content_type.split(";", 1)[0].strip().lower()
        if content_type not in SUPPORTED_TEXT_TYPES:
            return self._fail_result(ingestion, "unsupported_content_type")
        try:
            raw = storage.read_limited(workspace_file.storage_key, MAX_SOURCE_BYTES)
        except StorageObjectTooLargeError:
            return self._fail_result(ingestion, "source_too_large")
        except FileNotFoundError:
            return self._fail_result(ingestion, "source_object_not_found")
        except (OSError, StorageObjectReadError, ValueError):
            return self._fail_result(ingestion, "source_object_unreadable")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return self._fail_result(ingestion, "source_not_utf8")
        content_sha256 = hashlib.sha256(raw).hexdigest()
        if content_sha256 != workspace_file.checksum_sha256:
            return self._fail_result(ingestion, "source_checksum_mismatch")
        chunks = chunk_text(text)
        if not chunks:
            return self._fail_result(ingestion, "source_empty")
        self._archive_chunks(ingestion.workspace_id, ingestion.source_id)
        for index, chunk in enumerate(chunks):
            title = f"{source.name} #{index + 1}" if len(chunks) > 1 else source.name
            self._session.add(
                WorkspaceMemoryEntry(
                    workspace_id=ingestion.workspace_id,
                    source_type="knowledge_source",
                    source_id=str(source.id),
                    memory_layer="semantic",
                    scope_type="workspace",
                    scope_id=str(ingestion.workspace_id),
                    memory_key=f"knowledge_source:{source.id}:v{source.version}:{index}",
                    entry_type="knowledge_chunk",
                    title=title,
                    content=chunk,
                    tags=["knowledge_source", source.source_type],
                    visibility_scope="workspace",
                    importance=40,
                    status="active",
                    content_fingerprint=memory_content_fingerprint(title, chunk),
                    memory_metadata={
                        "knowledge_source_id": str(source.id),
                        "source_version": source.version,
                        "workspace_file_id": str(workspace_file.id),
                        "chunk_index": index,
                        "chunk_count": len(chunks),
                        "filename": workspace_file.filename,
                    },
                    embedding_status=initial_embedding_status(
                        self._session,
                        ingestion.workspace_id,
                    ),
                )
            )
        ingestion.status = "succeeded"
        ingestion.content_sha256 = content_sha256
        ingestion.byte_count = len(raw)
        ingestion.chunk_count = len(chunks)
        ingestion.completed_at = datetime.now(UTC)
        ingestion.error_code = None
        source.last_ingested_at = ingestion.completed_at
        source.last_error_code = None
        self._session.flush()
        AuditService(self._session).record_system_action(
            workspace_id=ingestion.workspace_id,
            action="knowledge_source.ingestion_succeeded",
            target_type="knowledge_source",
            target_id=source.id,
            metadata={
                "ingestion_id": str(ingestion.id),
                "source_version": source.version,
                "byte_count": ingestion.byte_count,
                "chunk_count": ingestion.chunk_count,
            },
        )
        return self._result(ingestion)

    def fail(self, ingestion: KnowledgeSourceIngestion, error_code: str) -> None:
        ingestion.status = "failed"
        ingestion.error_code = error_code
        ingestion.completed_at = datetime.now(UTC)
        source = self._session.scalar(
            select(KnowledgeSource).where(
                KnowledgeSource.workspace_id == ingestion.workspace_id,
                KnowledgeSource.id == ingestion.source_id,
            )
        )
        if source is not None:
            source.last_error_code = error_code
        AuditService(self._session).record_system_action(
            workspace_id=ingestion.workspace_id,
            action="knowledge_source.ingestion_failed",
            target_type="knowledge_source",
            target_id=ingestion.source_id,
            metadata={"ingestion_id": str(ingestion.id), "error_code": error_code},
        )

    def _fail_result(
        self,
        ingestion: KnowledgeSourceIngestion,
        error_code: str,
    ) -> KnowledgeIngestionResult:
        self.fail(ingestion, error_code)
        return self._result(ingestion)

    def _result(self, ingestion: KnowledgeSourceIngestion) -> KnowledgeIngestionResult:
        return KnowledgeIngestionResult(
            ingestion_id=ingestion.id,
            source_id=ingestion.source_id,
            source_version=ingestion.source_version,
            status=ingestion.status,
            byte_count=ingestion.byte_count,
            chunk_count=ingestion.chunk_count,
            error_code=ingestion.error_code,
        )

    def _lock_source(self, workspace_id: UUID, source_id: UUID) -> KnowledgeSource | None:
        return self._session.scalar(
            select(KnowledgeSource)
            .where(
                KnowledgeSource.workspace_id == workspace_id,
                KnowledgeSource.id == source_id,
            )
            .with_for_update()
        )

    def _archive_chunks(self, workspace_id: UUID, source_id: UUID) -> None:
        entries = self._session.scalars(
            select(WorkspaceMemoryEntry).where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.source_type == "knowledge_source",
                WorkspaceMemoryEntry.source_id == str(source_id),
                WorkspaceMemoryEntry.entry_type == "knowledge_chunk",
                WorkspaceMemoryEntry.status == "active",
            )
        ).all()
        now = datetime.now(UTC)
        for entry in entries:
            entry.status = "archived"
            entry.archived_at = now
            entry.invalidate_embedding(status="not_applicable")


def enqueue_knowledge_source_ingestion_job(
    *,
    queue: RedisQueue,
    workspace_id: UUID,
    source_id: UUID,
    source_version: int,
    requested_by_user_id: UUID,
) -> bool:
    return queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.KNOWLEDGE_INGEST,
            resource_id=source_id,
            requested_by_user_id=requested_by_user_id,
            idempotency_key=f"knowledge.ingest:{workspace_id}:{source_id}:v{source_version}",
            routing={"source_version": source_version},
            priority=-5,
            max_attempts=1,
        )
    )
