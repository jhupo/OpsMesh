from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.memory.models import (
    WorkspaceMemoryConfiguration,
    WorkspaceMemoryEmbeddingEvent,
    WorkspaceMemoryEntry,
)
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.model_providers.provider_keys import canonical_model_provider
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue


@dataclass(frozen=True, slots=True)
class MemoryEmbeddingScheduleSummary:
    enqueued: int
    already_queued: int
    recovered: int = 0


class WorkspaceMemoryEmbeddingScheduler:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue_pending(
        self,
        *,
        queue: RedisQueue,
        limit: int,
        stale_after_seconds: int = 900,
    ) -> MemoryEmbeddingScheduleSummary:
        if limit <= 0 or stale_after_seconds <= 0:
            return MemoryEmbeddingScheduleSummary(enqueued=0, already_queued=0)
        recovered = self._recover_stale_processing(
            stale_before=datetime.now(UTC) - timedelta(seconds=stale_after_seconds),
            limit=limit,
        )
        rows = self._session.execute(
            select(WorkspaceMemoryEntry, WorkspaceMemoryConfiguration)
            .join(
                WorkspaceMemoryConfiguration,
                WorkspaceMemoryConfiguration.workspace_id == WorkspaceMemoryEntry.workspace_id,
            )
            .where(
                WorkspaceMemoryConfiguration.embedding_enabled.is_(True),
                WorkspaceMemoryEntry.status == "active",
                WorkspaceMemoryEntry.memory_layer.in_(("episodic", "semantic")),
                WorkspaceMemoryEntry.embedding_status == "pending",
            )
            .order_by(
                WorkspaceMemoryEntry.importance.desc(),
                WorkspaceMemoryEntry.created_at.asc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        enqueued = 0
        already_queued = 0
        for entry, _configuration in rows:
            job = JobPayload(
                workspace_id=entry.workspace_id,
                job_type=JobType.MEMORY_EMBED,
                resource_id=entry.id,
                idempotency_key=(
                    f"memory.embed:{entry.workspace_id}:{entry.id}:"
                    f"{entry.embedding_generation}:{entry.content_fingerprint}"
                ),
                routing={
                    "embedding_generation": entry.embedding_generation,
                },
                priority=-9,
            )
            if queue.enqueue(job):
                enqueued += 1
            else:
                already_queued += 1
            entry.embedding_status = "queued"
        self._session.flush([entry for entry, _ in rows])
        return MemoryEmbeddingScheduleSummary(
            enqueued=enqueued,
            already_queued=already_queued,
            recovered=recovered,
        )

    def _recover_stale_processing(self, *, stale_before: datetime, limit: int) -> int:
        rows = self._session.execute(
            select(WorkspaceMemoryEntry, WorkspaceMemoryConfiguration)
            .join(
                WorkspaceMemoryConfiguration,
                WorkspaceMemoryConfiguration.workspace_id == WorkspaceMemoryEntry.workspace_id,
            )
            .where(
                WorkspaceMemoryConfiguration.embedding_enabled.is_(True),
                WorkspaceMemoryEntry.status == "active",
                WorkspaceMemoryEntry.memory_layer.in_(("episodic", "semantic")),
                WorkspaceMemoryEntry.embedding_status == "processing",
                WorkspaceMemoryEntry.embedding_processing_started_at <= stale_before,
            )
            .order_by(WorkspaceMemoryEntry.embedding_processing_started_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for entry, configuration in rows:
            entry.embedding_status = "pending"
            entry.embedding_generation += 1
            entry.embedding_last_error_code = "embedding_processing_abandoned"
            entry.embedding_processing_started_at = None
            credential = self._session.get(
                ModelProviderCredential,
                configuration.embedding_credential_id,
            )
            self._session.add(
                WorkspaceMemoryEmbeddingEvent(
                    workspace_id=entry.workspace_id,
                    memory_entry_id=entry.id,
                    credential_id=configuration.embedding_credential_id,
                    configuration_version=configuration.version,
                    embedding_generation=entry.embedding_generation,
                    provider=canonical_model_provider(
                        credential.provider if credential is not None else "openai"
                    ),
                    model=configuration.embedding_model,
                    dimensions=configuration.embedding_dimensions,
                    status="recovered",
                    error_code="embedding_processing_abandoned",
                )
            )
        self._session.flush()
        return len(rows)
