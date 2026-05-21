from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.domains.models import DomainItem
from backend.app.files.models import WorkspaceFile
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.tasks.models import Task, TaskMessage, TaskStep

_TOKEN_PATTERN = re.compile(r"[\w.-]+", re.UNICODE)
_SNIPPET_LENGTH = 220
_SOURCE_LIMIT = 80


@dataclass(frozen=True)
class _MemoryCandidate:
    source_type: str
    source_id: UUID
    title: str
    text: str
    created_at: datetime | None
    metadata: dict[str, object]


class WorkspaceMemorySearchService:
    """Lightweight workspace-scoped lexical search across operational memory."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def search(
        self,
        *,
        workspace_id: UUID,
        query: str,
        limit: int = 10,
        source_types: set[str] | None = None,
    ) -> list[dict[str, object]]:
        terms = _query_terms(query)
        if not terms or limit <= 0:
            return []

        results: list[tuple[int, _MemoryCandidate]] = []
        indexed_sources = self._indexed_sources(workspace_id)
        for candidate in self._candidates(workspace_id):
            if source_types is not None and candidate.source_type not in source_types:
                continue
            if (
                candidate.source_type,
                str(candidate.source_id),
            ) in indexed_sources and not candidate.metadata.get("indexed"):
                continue
            score = _score(candidate, terms, query)
            if score > 0:
                results.append((score, candidate))

        results.sort(
            key=lambda item: (
                item[0],
                _created_at_sort_key(item[1].created_at),
                item[1].title.lower(),
            ),
            reverse=True,
        )
        return [_result_payload(candidate, score, terms) for score, candidate in results[:limit]]

    def _candidates(self, workspace_id: UUID) -> list[_MemoryCandidate]:
        return [
            *self._explicit_memory_candidates(workspace_id),
            *self._file_candidates(workspace_id),
            *self._artifact_candidates(workspace_id),
            *self._task_candidates(workspace_id),
            *self._task_step_candidates(workspace_id),
            *self._task_message_candidates(workspace_id),
            *self._domain_item_candidates(workspace_id),
        ]

    def _indexed_sources(self, workspace_id: UUID) -> set[tuple[str, str]]:
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

    def _explicit_memory_candidates(self, workspace_id: UUID) -> list[_MemoryCandidate]:
        entries = self._session.scalars(
            select(WorkspaceMemoryEntry)
            .where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.status == "active",
            )
            .order_by(
                WorkspaceMemoryEntry.importance.desc(),
                WorkspaceMemoryEntry.updated_at.desc(),
            )
            .limit(_SOURCE_LIMIT)
        ).all()
        return [
            _MemoryCandidate(
                source_type=_memory_entry_source_type(entry),
                source_id=_memory_entry_source_id(entry),
                title=entry.title,
                text=_join_text(
                    entry.title,
                    entry.content,
                    entry.entry_type,
                    _json_text(entry.tags),
                    _json_text(entry.memory_metadata),
                ),
                created_at=entry.created_at,
                metadata={
                    "entry_type": entry.entry_type,
                    "tags": entry.tags,
                    "visibility_scope": entry.visibility_scope,
                    "importance": entry.importance,
                    "source_type": entry.source_type,
                    "source_id": entry.source_id,
                    **entry.memory_metadata,
                },
            )
            for entry in entries
        ]

    def _file_candidates(self, workspace_id: UUID) -> list[_MemoryCandidate]:
        files = self._session.scalars(
            select(WorkspaceFile)
            .where(
                WorkspaceFile.workspace_id == workspace_id,
                WorkspaceFile.status == "active",
            )
            .order_by(WorkspaceFile.created_at.desc())
            .limit(_SOURCE_LIMIT)
        ).all()
        return [
            _MemoryCandidate(
                source_type="workspace_file",
                source_id=file.id,
                title=file.filename,
                text=_join_text(
                    file.filename,
                    file.content_type,
                    file.checksum_sha256,
                    _json_text(file.file_metadata),
                ),
                created_at=file.created_at,
                metadata={
                    "content_type": file.content_type,
                    "size_bytes": file.size_bytes,
                },
            )
            for file in files
        ]

    def _artifact_candidates(self, workspace_id: UUID) -> list[_MemoryCandidate]:
        artifacts = self._session.scalars(
            select(Artifact)
            .where(Artifact.workspace_id == workspace_id)
            .order_by(Artifact.created_at.desc())
            .limit(_SOURCE_LIMIT)
        ).all()
        return [
            _MemoryCandidate(
                source_type="artifact",
                source_id=artifact.id,
                title=artifact.filename,
                text=_join_text(
                    artifact.filename,
                    artifact.artifact_type,
                    artifact.content_type,
                    artifact.checksum_sha256,
                    _json_text(artifact.artifact_metadata),
                ),
                created_at=artifact.created_at,
                metadata={
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
                },
            )
            for artifact in artifacts
        ]

    def _task_candidates(self, workspace_id: UUID) -> list[_MemoryCandidate]:
        tasks = self._session.scalars(
            select(Task)
            .where(Task.workspace_id == workspace_id)
            .order_by(Task.created_at.desc())
            .limit(_SOURCE_LIMIT)
        ).all()
        return [
            _MemoryCandidate(
                source_type="task",
                source_id=task.id,
                title=task.title,
                text=_join_text(
                    task.title,
                    task.description,
                    task.status,
                    task.domain_type,
                    _json_text(task.input),
                    _json_text(task.generic_state),
                    _json_text(task.domain_state),
                    _json_text(task.final_output),
                ),
                created_at=task.created_at,
                metadata={
                    "status": task.status,
                    "domain_type": task.domain_type,
                    "agent_team_id": _str_or_none(task.agent_team_id),
                },
            )
            for task in tasks
        ]

    def _task_step_candidates(self, workspace_id: UUID) -> list[_MemoryCandidate]:
        steps = self._session.scalars(
            select(TaskStep)
            .where(TaskStep.workspace_id == workspace_id)
            .order_by(TaskStep.created_at.desc())
            .limit(_SOURCE_LIMIT)
        ).all()
        return [
            _MemoryCandidate(
                source_type="task_step",
                source_id=step.id,
                title=step.title,
                text=_join_text(
                    step.title,
                    step.description,
                    step.status,
                    step.required_role,
                    step.result_summary,
                    _json_text(step.required_skills),
                    _json_text(step.expected_artifacts),
                    _json_text(step.acceptance_criteria),
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

    def _task_message_candidates(self, workspace_id: UUID) -> list[_MemoryCandidate]:
        messages = self._session.scalars(
            select(TaskMessage)
            .where(TaskMessage.workspace_id == workspace_id)
            .order_by(TaskMessage.created_at.desc())
            .limit(_SOURCE_LIMIT)
        ).all()
        return [
            _MemoryCandidate(
                source_type="task_message",
                source_id=message.id,
                title=f"{message.message_type} #{message.sequence}",
                text=_join_text(message.message_type, message.body, _json_text(message.payload)),
                created_at=message.created_at,
                metadata={
                    "task_id": str(message.task_id),
                    "task_step_id": _str_or_none(message.task_step_id),
                    "agent_run_id": _str_or_none(message.agent_run_id),
                    "agent_profile_id": _str_or_none(message.agent_profile_id),
                    "message_type": message.message_type,
                    "sequence": message.sequence,
                },
            )
            for message in messages
        ]

    def _domain_item_candidates(self, workspace_id: UUID) -> list[_MemoryCandidate]:
        items = self._session.scalars(
            select(DomainItem)
            .where(DomainItem.workspace_id == workspace_id)
            .order_by(DomainItem.created_at.desc())
            .limit(_SOURCE_LIMIT)
        ).all()
        return [
            _MemoryCandidate(
                source_type="domain_item",
                source_id=item.id,
                title=item.title,
                text=_join_text(
                    item.title,
                    item.item_type,
                    item.status,
                    _json_text(item.content),
                    _json_text(item.state),
                ),
                created_at=item.created_at,
                metadata={
                    "domain_project_id": _str_or_none(item.domain_project_id),
                    "task_id": _str_or_none(item.task_id),
                    "parent_item_id": _str_or_none(item.parent_item_id),
                    "item_type": item.item_type,
                    "status": item.status,
                },
            )
            for item in items
        ]


def _query_terms(query: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for token in _TOKEN_PATTERN.findall(query.lower()):
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms


def _score(candidate: _MemoryCandidate, terms: list[str], query: str) -> int:
    title = candidate.title.lower()
    text = candidate.text.lower()
    phrase = " ".join(_query_terms(query))
    score = 0
    if phrase and phrase in title:
        score += 30
    elif phrase and phrase in text:
        score += 16
    for term in terms:
        if term in title:
            score += 10
        if term in text:
            score += 3
    return score


def _result_payload(
    candidate: _MemoryCandidate,
    score: int,
    terms: list[str],
) -> dict[str, object]:
    return {
        "source_type": candidate.source_type,
        "source_id": str(candidate.source_id),
        "title": candidate.title,
        "snippet": _snippet(candidate.text, terms),
        "score": score,
        "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
        "metadata": candidate.metadata,
    }


def _snippet(text: str, terms: list[str]) -> str:
    collapsed = " ".join(text.split())
    lower = collapsed.lower()
    positions = [lower.find(term) for term in terms if term in lower]
    start = max(0, min(positions) - 60) if positions else 0
    snippet = collapsed[start : start + _SNIPPET_LENGTH]
    if start > 0:
        snippet = f"...{snippet}"
    if start + _SNIPPET_LENGTH < len(collapsed):
        snippet = f"{snippet}..."
    return snippet


def _join_text(*parts: object) -> str:
    return " ".join(str(part) for part in parts if part not in (None, ""))


def _json_text(value: object) -> str:
    if value in (None, "", [], {}):
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _str_or_none(value: object) -> str | None:
    return str(value) if value is not None else None


def _memory_entry_source_type(entry: WorkspaceMemoryEntry) -> str:
    if entry.entry_type == "indexed_chunk" and entry.source_type:
        return entry.source_type
    return "workspace_memory"


def _memory_entry_source_id(entry: WorkspaceMemoryEntry) -> UUID:
    if entry.entry_type == "indexed_chunk" and entry.source_id:
        try:
            return UUID(entry.source_id)
        except ValueError:
            return entry.id
    return entry.id


def _created_at_sort_key(value: datetime | None) -> float:
    if value is None:
        return 0
    return value.timestamp()
