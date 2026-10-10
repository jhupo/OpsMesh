from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.orchestration.runs.models import AgentRun
from backend.app.resources.memory.configuration import initial_embedding_status
from backend.app.resources.memory.models import WorkspaceMemoryEntry
from backend.app.shared.utils import ensure_aware_utc

WORKING_MEMORY_LAYER = "working"
WORKING_MEMORY_SOURCE = "run_working_memory"


class AgentWorkingMemoryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def active_for_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
    ) -> list[WorkspaceMemoryEntry]:
        now = datetime.now(UTC)
        entries = list(
            self._session.scalars(
                select(WorkspaceMemoryEntry)
                .where(
                    WorkspaceMemoryEntry.workspace_id == workspace_id,
                    WorkspaceMemoryEntry.memory_layer == WORKING_MEMORY_LAYER,
                    WorkspaceMemoryEntry.scope_type == "run",
                    WorkspaceMemoryEntry.scope_id == str(run_id),
                    WorkspaceMemoryEntry.status == "active",
                )
                .order_by(WorkspaceMemoryEntry.created_at.asc(), WorkspaceMemoryEntry.id.asc())
            ).all()
        )
        active: list[WorkspaceMemoryEntry] = []
        for entry in entries:
            if entry.expires_at is not None and ensure_aware_utc(entry.expires_at) <= now:
                entry.status = "expired"
                entry.archived_at = now
            else:
                active.append(entry)
        self._session.flush(entries)
        return active

    def promote(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        memory_entry_id: UUID,
    ) -> WorkspaceMemoryEntry:
        source = self._session.scalar(
            select(WorkspaceMemoryEntry).where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.id == memory_entry_id,
                WorkspaceMemoryEntry.memory_layer == WORKING_MEMORY_LAYER,
                WorkspaceMemoryEntry.scope_type == "run",
                WorkspaceMemoryEntry.scope_id == str(run_id),
            )
        )
        if source is None:
            raise ValueError("Working memory entry not found in current run")
        existing = self._session.scalar(
            select(WorkspaceMemoryEntry).where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.memory_layer == "episodic",
                WorkspaceMemoryEntry.source_type == "working_memory_promotion",
                WorkspaceMemoryEntry.source_id == str(source.id),
            )
        )
        if existing is not None:
            return existing
        if source.status != "active":
            raise ValueError("Only active working memory can be promoted")
        run = self._session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.id == run_id,
            )
        )
        if run is None:
            raise ValueError("Working memory run not found")
        scope_type = "task" if run.task_id is not None else "agent"
        scope_id = str(run.task_id or run.agent_profile_id or run.id)
        promoted = WorkspaceMemoryEntry(
            workspace_id=workspace_id,
            created_by_agent_profile_id=run.agent_profile_id,
            created_by_agent_run_id=run.id,
            source_type="working_memory_promotion",
            source_id=str(source.id),
            memory_layer="episodic",
            scope_type=scope_type,
            scope_id=scope_id,
            memory_key=f"working:{source.id}",
            entry_type=source.entry_type,
            title=source.title,
            content=source.content,
            tags=list(dict.fromkeys([*source.tags, "promoted"])),
            visibility_scope=scope_type,
            importance=max(1, source.importance),
            status="active",
            revision=1,
            content_fingerprint=source.content_fingerprint,
            memory_metadata={
                **dict(source.memory_metadata),
                "promotion": {
                    "source_memory_id": str(source.id),
                    "source_run_id": str(run.id),
                    "promoted_at": datetime.now(UTC).isoformat(),
                },
            },
            embedding_status=initial_embedding_status(self._session, workspace_id),
        )
        self._session.add(promoted)
        self._session.flush([promoted])
        source.status = "promoted"
        source.archived_at = datetime.now(UTC)
        source.memory_metadata = {
            **dict(source.memory_metadata),
            "promoted_to_memory_id": str(promoted.id),
        }
        self._session.flush([source])
        return promoted

    def expire_run(self, *, workspace_id: UUID, run_id: UUID) -> int:
        entries = self.active_for_run(workspace_id=workspace_id, run_id=run_id)
        now = datetime.now(UTC)
        for entry in entries:
            entry.status = "expired"
            entry.archived_at = now
        self._session.flush(entries)
        return len(entries)
