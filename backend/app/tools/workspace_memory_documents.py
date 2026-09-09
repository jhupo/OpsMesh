from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.domains.models import DomainItem
from backend.app.files.models import WorkspaceFile
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.memory.search import (
    MemorySearchDocument,
    MemorySearchHit,
    memory_entry_document_metadata,
)
from backend.app.tasks.models import Task, TaskMessage, TaskStep

SOURCE_LIMIT = 80


class WorkspaceMemoryDocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def candidates(self, workspace_id: UUID) -> list[MemorySearchDocument]:
        return [
            *self.explicit_memory_candidates(workspace_id),
            *self.file_candidates(workspace_id),
            *self.artifact_candidates(workspace_id),
            *self.task_candidates(workspace_id),
            *self.task_step_candidates(workspace_id),
            *self.task_message_candidates(workspace_id),
            *self.domain_item_candidates(workspace_id),
        ]

    def indexed_sources(self, workspace_id: UUID) -> set[tuple[str, str]]:
        rows = self._session.scalars(
            select(WorkspaceMemoryEntry).where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.entry_type == "indexed_chunk",
                WorkspaceMemoryEntry.status == "active",
                WorkspaceMemoryEntry.source_type.is_not(None),
                WorkspaceMemoryEntry.source_id.is_not(None),
            )
        ).all()
        return {
            (entry.source_type, entry.source_id)
            for entry in rows
            if entry.source_type is not None and entry.source_id is not None
        }

    def explicit_memory_candidates(self, workspace_id: UUID) -> list[MemorySearchDocument]:
        entries = self._session.scalars(
            select(WorkspaceMemoryEntry)
            .where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.status == "active",
                WorkspaceMemoryEntry.memory_layer.in_(("episodic", "semantic")),
                or_(
                    WorkspaceMemoryEntry.expires_at.is_(None),
                    WorkspaceMemoryEntry.expires_at > datetime.now(UTC),
                ),
            )
            .order_by(
                WorkspaceMemoryEntry.importance.desc(),
                WorkspaceMemoryEntry.updated_at.desc(),
            )
            .limit(SOURCE_LIMIT)
        ).all()
        return [
            MemorySearchDocument(
                source_type=memory_entry_source_type(entry),
                source_id=memory_entry_source_id(entry),
                title=entry.title,
                text=join_text(
                    entry.title,
                    entry.content,
                    entry.entry_type,
                    json_text(entry.tags),
                    json_text(entry.memory_metadata),
                ),
                created_at=entry.created_at,
                metadata=memory_entry_document_metadata(entry),
            )
            for entry in entries
        ]

    def file_candidates(self, workspace_id: UUID) -> list[MemorySearchDocument]:
        files = self._session.scalars(
            select(WorkspaceFile)
            .where(
                WorkspaceFile.workspace_id == workspace_id,
                WorkspaceFile.status == "active",
            )
            .order_by(WorkspaceFile.created_at.desc())
            .limit(SOURCE_LIMIT)
        ).all()
        return [
            MemorySearchDocument(
                source_type="workspace_file",
                source_id=file.id,
                title=file.filename,
                text=join_text(
                    file.filename,
                    file.content_type,
                    file.checksum_sha256,
                    json_text(file.file_metadata),
                ),
                created_at=file.created_at,
                metadata={
                    "content_type": file.content_type,
                    "size_bytes": file.size_bytes,
                },
            )
            for file in files
        ]

    def artifact_candidates(self, workspace_id: UUID) -> list[MemorySearchDocument]:
        artifacts = self._session.scalars(
            select(Artifact)
            .where(Artifact.workspace_id == workspace_id)
            .order_by(Artifact.created_at.desc())
            .limit(SOURCE_LIMIT)
        ).all()
        return [
            MemorySearchDocument(
                source_type="artifact",
                source_id=artifact.id,
                title=artifact.filename,
                text=join_text(
                    artifact.filename,
                    artifact.artifact_type,
                    artifact.content_type,
                    artifact.checksum_sha256,
                    json_text(artifact.artifact_metadata),
                ),
                created_at=artifact.created_at,
                metadata={
                    "task_id": str_or_none(artifact.task_id),
                    "agent_run_id": str_or_none(artifact.agent_run_id),
                    "task_step_id": str_or_none(artifact.task_step_id),
                    "agent_profile_id": str_or_none(artifact.agent_profile_id),
                    "work_package_id": artifact.work_package_id,
                    "version": artifact.version,
                    "supersedes_artifact_id": str_or_none(artifact.supersedes_artifact_id),
                    "review_status": artifact.review_status,
                    "artifact_type": artifact.artifact_type,
                    "content_type": artifact.content_type,
                    "size_bytes": artifact.size_bytes,
                },
            )
            for artifact in artifacts
        ]

    def task_candidates(self, workspace_id: UUID) -> list[MemorySearchDocument]:
        tasks = self._session.scalars(
            select(Task)
            .where(Task.workspace_id == workspace_id)
            .order_by(Task.created_at.desc())
            .limit(SOURCE_LIMIT)
        ).all()
        return [
            MemorySearchDocument(
                source_type="task",
                source_id=task.id,
                title=task.title,
                text=join_text(
                    task.title,
                    task.description,
                    task.status,
                    task.domain_type,
                    json_text(task.input),
                    json_text(task.generic_state),
                    json_text(task.domain_state),
                    json_text(task.final_output),
                ),
                created_at=task.created_at,
                metadata={
                    "status": task.status,
                    "domain_type": task.domain_type,
                    "agent_team_id": str_or_none(task.agent_team_id),
                },
            )
            for task in tasks
        ]

    def task_step_candidates(self, workspace_id: UUID) -> list[MemorySearchDocument]:
        steps = self._session.scalars(
            select(TaskStep)
            .where(TaskStep.workspace_id == workspace_id)
            .order_by(TaskStep.created_at.desc())
            .limit(SOURCE_LIMIT)
        ).all()
        return [
            MemorySearchDocument(
                source_type="task_step",
                source_id=step.id,
                title=step.title,
                text=join_text(
                    step.title,
                    step.description,
                    step.status,
                    step.required_role,
                    step.result_summary,
                    json_text(step.required_skills),
                    json_text(step.expected_artifacts),
                    json_text(step.acceptance_criteria),
                ),
                created_at=step.created_at,
                metadata={
                    "task_id": str(step.task_id),
                    "status": step.status,
                    "required_role": step.required_role,
                    "work_package_id": step.work_package_id,
                },
            )
            for step in steps
        ]

    def task_message_candidates(self, workspace_id: UUID) -> list[MemorySearchDocument]:
        messages = self._session.scalars(
            select(TaskMessage)
            .where(TaskMessage.workspace_id == workspace_id)
            .order_by(TaskMessage.created_at.desc())
            .limit(SOURCE_LIMIT)
        ).all()
        return [
            MemorySearchDocument(
                source_type="task_message",
                source_id=message.id,
                title=f"{message.message_type} #{message.sequence}",
                text=join_text(message.message_type, message.body, json_text(message.payload)),
                created_at=message.created_at,
                metadata={
                    "task_id": str(message.task_id),
                    "task_step_id": str_or_none(message.task_step_id),
                    "agent_run_id": str_or_none(message.agent_run_id),
                    "agent_profile_id": str_or_none(message.agent_profile_id),
                    "message_type": message.message_type,
                    "sequence": message.sequence,
                },
            )
            for message in messages
        ]

    def domain_item_candidates(self, workspace_id: UUID) -> list[MemorySearchDocument]:
        items = self._session.scalars(
            select(DomainItem)
            .where(DomainItem.workspace_id == workspace_id)
            .order_by(DomainItem.created_at.desc())
            .limit(SOURCE_LIMIT)
        ).all()
        return [
            MemorySearchDocument(
                source_type="domain_item",
                source_id=item.id,
                title=item.title,
                text=join_text(
                    item.title,
                    item.item_type,
                    item.status,
                    json_text(item.content),
                    json_text(item.state),
                ),
                created_at=item.created_at,
                metadata={
                    "domain_project_id": str_or_none(item.domain_project_id),
                    "task_id": str_or_none(item.task_id),
                    "parent_item_id": str_or_none(item.parent_item_id),
                    "item_type": item.item_type,
                    "status": item.status,
                },
            )
            for item in items
        ]


def result_payload(hit: MemorySearchHit) -> dict[str, object]:
    document = hit.document
    return {
        "source_type": document.source_type,
        "source_id": str(document.source_id),
        "resource_type": document.source_type,
        "resource_id": str(document.source_id),
        "title": document.title,
        "snippet": hit.snippet,
        "score": hit.score,
        "search_backend": hit.backend_name,
        "ranking": hit.ranking_details,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "metadata": document.metadata,
    }


def join_text(*parts: object) -> str:
    return " ".join(str(part) for part in parts if part not in (None, ""))


def json_text(value: object) -> str:
    if value in (None, "", [], {}):
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def str_or_none(value: object) -> str | None:
    return str(value) if value is not None else None


def memory_entry_source_type(entry: WorkspaceMemoryEntry) -> str:
    if entry.entry_type == "indexed_chunk" and entry.source_type:
        return entry.source_type
    return "workspace_memory"


def memory_entry_source_id(entry: WorkspaceMemoryEntry) -> UUID:
    if entry.entry_type == "indexed_chunk" and entry.source_id:
        try:
            return UUID(entry.source_id)
        except ValueError:
            return entry.id
    return entry.id
