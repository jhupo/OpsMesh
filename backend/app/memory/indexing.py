from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.files.models import WorkspaceFile
from backend.app.files.runtime_policy import runtime_file_denial_code
from backend.app.memory.content import memory_content_fingerprint
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.tasks.models import Task

_CHUNK_SIZE = 900
_CHUNK_OVERLAP = 120


@dataclass(frozen=True)
class WorkspaceMemoryIndexResult:
    source_type: str
    source_id: str
    archived_chunks: int
    indexed_chunks: int


class WorkspaceMemoryIndexingService:
    """Build deterministic workspace-memory chunks from durable workspace objects."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def refresh_task(self, *, workspace_id: UUID, task_id: UUID) -> WorkspaceMemoryIndexResult:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return WorkspaceMemoryIndexResult("task", str(task_id), 0, 0)
        text = _join_text(
            task.title,
            task.description,
            task.status,
            task.domain_type,
            _json_text(task.input),
            _json_text(task.generic_state),
            _json_text(task.domain_state),
            _json_text(task.final_output),
        )
        metadata = {
            "status": task.status,
            "domain_type": task.domain_type,
            "agent_team_id": _str_or_none(task.agent_team_id),
        }
        return self._refresh_source(
            workspace_id=workspace_id,
            source_type="task",
            source_id=task.id,
            title=task.title,
            text=text,
            metadata=metadata,
            source_updated_at=task.updated_at,
        )

    def refresh_file(self, *, workspace_id: UUID, file_id: UUID) -> WorkspaceMemoryIndexResult:
        file = self._session.scalar(
            select(WorkspaceFile).where(
                WorkspaceFile.workspace_id == workspace_id,
                WorkspaceFile.id == file_id,
                WorkspaceFile.status == "active",
            )
        )
        if file is None:
            return WorkspaceMemoryIndexResult("workspace_file", str(file_id), 0, 0)
        try:
            denied = runtime_file_denial_code(file) is not None
        except ValueError:
            denied = True
        if denied:
            archived = self._archive_existing_chunks(
                workspace_id=workspace_id,
                source_type="workspace_file",
                source_id=str(file.id),
            )
            return WorkspaceMemoryIndexResult(
                "workspace_file",
                str(file.id),
                archived,
                0,
            )
        text = _join_text(
            file.filename,
            file.content_type,
            file.checksum_sha256,
            _json_text(file.file_metadata),
        )
        return self._refresh_source(
            workspace_id=workspace_id,
            source_type="workspace_file",
            source_id=file.id,
            title=file.filename,
            text=text,
            metadata={
                "content_type": file.content_type,
                "size_bytes": file.size_bytes,
                "sensitivity": file.sensitivity,
            },
            source_updated_at=file.updated_at,
        )

    def refresh_artifact(
        self,
        *,
        workspace_id: UUID,
        artifact_id: UUID,
    ) -> WorkspaceMemoryIndexResult:
        artifact = self._session.scalar(
            select(Artifact).where(
                Artifact.workspace_id == workspace_id,
                Artifact.id == artifact_id,
            )
        )
        if artifact is None:
            return WorkspaceMemoryIndexResult("artifact", str(artifact_id), 0, 0)
        text = _join_text(
            artifact.filename,
            artifact.artifact_type,
            artifact.content_type,
            artifact.checksum_sha256,
            _json_text(artifact.artifact_metadata),
        )
        metadata = {
            "task_id": _str_or_none(artifact.task_id),
            "agent_run_id": _str_or_none(artifact.agent_run_id),
            "task_step_id": _str_or_none(artifact.task_step_id),
            "agent_profile_id": _str_or_none(artifact.agent_profile_id),
            "work_package_id": artifact.work_package_id,
            "version": artifact.version,
            "supersedes_artifact_id": _str_or_none(artifact.supersedes_artifact_id),
            "review_status": artifact.review_status,
            "artifact_type": artifact.artifact_type,
            "content_type": artifact.content_type,
            "size_bytes": artifact.size_bytes,
        }
        return self._refresh_source(
            workspace_id=workspace_id,
            source_type="artifact",
            source_id=artifact.id,
            title=artifact.filename,
            text=text,
            metadata=metadata,
            source_updated_at=artifact.created_at,
        )

    def _refresh_source(
        self,
        *,
        workspace_id: UUID,
        source_type: str,
        source_id: UUID,
        title: str,
        text: str,
        metadata: dict[str, object],
        source_updated_at: datetime | None,
    ) -> WorkspaceMemoryIndexResult:
        source_id_text = str(source_id)
        archived = self._archive_existing_chunks(
            workspace_id=workspace_id,
            source_type=source_type,
            source_id=source_id_text,
        )
        chunks = _chunks(text)
        for index, chunk in enumerate(chunks):
            entry = WorkspaceMemoryEntry(
                workspace_id=workspace_id,
                source_type=source_type,
                source_id=source_id_text,
                memory_layer="semantic",
                scope_type="workspace",
                scope_id=str(workspace_id),
                entry_type="indexed_chunk",
                title=f"{title} #{index + 1}" if len(chunks) > 1 else title,
                content=chunk,
                tags=[source_type],
                visibility_scope="workspace",
                importance=_importance(source_type),
                status="active",
                content_fingerprint=memory_content_fingerprint(
                    f"{title} #{index + 1}" if len(chunks) > 1 else title,
                    chunk,
                ),
                memory_metadata={
                    **metadata,
                    "indexed": True,
                    "chunk_index": index,
                    "chunk_count": len(chunks),
                    "source_updated_at": _dt_or_none(source_updated_at),
                    "indexed_at": datetime.now(UTC).isoformat(),
                },
            )
            self._session.add(entry)
        self._session.flush()
        return WorkspaceMemoryIndexResult(source_type, source_id_text, archived, len(chunks))

    def _archive_existing_chunks(
        self,
        *,
        workspace_id: UUID,
        source_type: str,
        source_id: str,
    ) -> int:
        entries = self._session.scalars(
            select(WorkspaceMemoryEntry).where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.source_type == source_type,
                WorkspaceMemoryEntry.source_id == source_id,
                WorkspaceMemoryEntry.entry_type == "indexed_chunk",
                WorkspaceMemoryEntry.status == "active",
            )
        ).all()
        for entry in entries:
            entry.status = "archived"
            entry.archived_at = datetime.now(UTC)
        return len(entries)


def _chunks(text: str) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + _CHUNK_SIZE)
        chunks.append(normalized[start:end])
        if end == len(normalized):
            break
        start = max(end - _CHUNK_OVERLAP, start + 1)
    return chunks


def _join_text(*parts: object | None) -> str:
    return " ".join(str(part).strip() for part in parts if str(part or "").strip())


def _json_text(value: object) -> str:
    if value in (None, {}, []):
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _str_or_none(value: object | None) -> str | None:
    return str(value) if value is not None else None


def _dt_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _importance(source_type: str) -> int:
    return {
        "task": 50,
        "artifact": 45,
        "workspace_file": 35,
    }.get(source_type, 25)
