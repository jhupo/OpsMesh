from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.redaction import redact_sensitive_payload
from backend.app.memory.content import memory_content_fingerprint
from backend.app.memory.models import WorkspaceMemoryEntry, WorkspaceMemoryVersion
from backend.app.runs.models import AgentRun
from backend.app.security.redaction import redact_text_fragments
from backend.app.teams.models import AgentTeam

SEMANTIC_SCOPE_TYPES = frozenset({"workspace", "team", "agent"})
SEMANTIC_KNOWLEDGE_TYPES = frozenset({"fact", "configuration", "policy", "procedure"})


class SemanticMemoryConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SemanticMemoryUpsert:
    workspace_id: UUID
    scope_type: str
    scope_id: UUID
    memory_key: str
    knowledge_type: str
    title: str
    content: str
    tags: list[str]
    importance: int
    metadata: dict[str, object]
    expected_revision: int | None = None
    changed_by_user_id: UUID | None = None
    changed_by_agent_profile_id: UUID | None = None
    changed_by_agent_run_id: UUID | None = None
    change_reason: str | None = None


class AgentSemanticMemoryService:
    """Owns scoped semantic knowledge heads and immutable revision history."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, command: SemanticMemoryUpsert) -> WorkspaceMemoryEntry:
        scope_type, scope_id = self._validate_scope(command)
        memory_key = _required_text(command.memory_key, 160, "memory_key")
        knowledge_type = command.knowledge_type.strip().lower()
        if knowledge_type not in SEMANTIC_KNOWLEDGE_TYPES:
            raise ValueError(f"Unsupported semantic knowledge type: {knowledge_type}")
        title = redact_text_fragments(_required_text(command.title, 240, "title"))
        content = redact_text_fragments(_required_text(command.content, 100_000, "content"))
        tags = _tags(command.tags)
        metadata = redact_sensitive_payload(command.metadata)
        existing = self._session.scalar(
            select(WorkspaceMemoryEntry)
            .where(
                WorkspaceMemoryEntry.workspace_id == command.workspace_id,
                WorkspaceMemoryEntry.memory_layer == "semantic",
                WorkspaceMemoryEntry.scope_type == scope_type,
                WorkspaceMemoryEntry.scope_id == scope_id,
                WorkspaceMemoryEntry.memory_key == memory_key,
            )
            .with_for_update()
        )
        if existing is None:
            if command.expected_revision not in {None, 0}:
                raise SemanticMemoryConflictError(
                    "Semantic memory does not exist at the expected revision"
                )
            entry = WorkspaceMemoryEntry(
                workspace_id=command.workspace_id,
                created_by_user_id=command.changed_by_user_id,
                created_by_agent_profile_id=command.changed_by_agent_profile_id,
                created_by_agent_run_id=command.changed_by_agent_run_id,
                source_type="semantic_memory",
                source_id=None,
                memory_layer="semantic",
                scope_type=scope_type,
                scope_id=scope_id,
                memory_key=memory_key,
                entry_type=f"semantic_{knowledge_type}",
                title=title,
                content=content,
                tags=tags,
                visibility_scope=scope_type,
                importance=_importance(command.importance),
                status="active",
                revision=1,
                content_fingerprint=memory_content_fingerprint(title, content),
                memory_metadata=metadata,
            )
            try:
                with self._session.begin_nested():
                    self._session.add(entry)
                    self._session.flush([entry])
            except IntegrityError as exc:
                raise SemanticMemoryConflictError(
                    "Semantic memory was created concurrently; retry with its current revision"
                ) from exc
            entry.source_id = str(entry.id)
            self._record_version(entry, command)
            return entry
        candidate = _semantic_snapshot_values(
            knowledge_type=knowledge_type,
            title=title,
            content=content,
            tags=tags,
            importance=_importance(command.importance),
            metadata=metadata,
            status="active",
        )
        if _head_values(existing) == candidate:
            return existing
        if command.expected_revision is not None and existing.revision != command.expected_revision:
            raise SemanticMemoryConflictError(
                f"Semantic memory revision is {existing.revision}, not {command.expected_revision}"
            )
        existing.entry_type = f"semantic_{knowledge_type}"
        existing.title = title
        existing.content = content
        existing.tags = tags
        existing.importance = _importance(command.importance)
        existing.memory_metadata = metadata
        existing.status = "active"
        existing.archived_at = None
        existing.content_fingerprint = memory_content_fingerprint(title, content)
        existing.revision += 1
        self._session.flush([existing])
        self._record_version(existing, command)
        return existing

    def archive(
        self,
        entry: WorkspaceMemoryEntry,
        *,
        expected_revision: int,
        changed_by_user_id: UUID | None = None,
        changed_by_agent_profile_id: UUID | None = None,
        changed_by_agent_run_id: UUID | None = None,
        change_reason: str | None = None,
    ) -> WorkspaceMemoryEntry:
        locked_entry = self._session.scalar(
            select(WorkspaceMemoryEntry)
            .where(
                WorkspaceMemoryEntry.workspace_id == entry.workspace_id,
                WorkspaceMemoryEntry.id == entry.id,
                WorkspaceMemoryEntry.memory_layer == "semantic",
                WorkspaceMemoryEntry.source_type == "semantic_memory",
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if locked_entry is None:
            raise ValueError("Only semantic memory can be version-archived")
        entry = locked_entry
        if entry.revision != expected_revision:
            raise SemanticMemoryConflictError(
                f"Semantic memory revision is {entry.revision}, not {expected_revision}"
            )
        if entry.status == "archived":
            return entry
        entry.status = "archived"
        entry.archived_at = datetime.now(UTC)
        entry.revision += 1
        self._session.flush([entry])
        self._record_version(
            entry,
            SemanticMemoryUpsert(
                workspace_id=entry.workspace_id,
                scope_type=entry.scope_type,
                scope_id=UUID(entry.scope_id),
                memory_key=entry.memory_key or str(entry.id),
                knowledge_type=entry.entry_type.removeprefix("semantic_"),
                title=entry.title,
                content=entry.content,
                tags=entry.tags,
                importance=entry.importance,
                metadata=entry.memory_metadata,
                changed_by_user_id=changed_by_user_id,
                changed_by_agent_profile_id=changed_by_agent_profile_id,
                changed_by_agent_run_id=changed_by_agent_run_id,
                change_reason=change_reason,
            ),
        )
        return entry

    def history(
        self,
        *,
        workspace_id: UUID,
        memory_entry_id: UUID,
    ) -> list[WorkspaceMemoryVersion]:
        return list(
            self._session.scalars(
                select(WorkspaceMemoryVersion)
                .where(
                    WorkspaceMemoryVersion.workspace_id == workspace_id,
                    WorkspaceMemoryVersion.memory_entry_id == memory_entry_id,
                )
                .order_by(WorkspaceMemoryVersion.revision.desc())
            ).all()
        )

    def get(
        self,
        *,
        workspace_id: UUID,
        memory_entry_id: UUID,
    ) -> WorkspaceMemoryEntry | None:
        return self._session.scalar(
            select(WorkspaceMemoryEntry).where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.id == memory_entry_id,
                WorkspaceMemoryEntry.memory_layer == "semantic",
                WorkspaceMemoryEntry.source_type == "semantic_memory",
            )
        )

    def list(
        self,
        *,
        workspace_id: UUID,
        scope_type: str | None,
        scope_id: UUID | None,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkspaceMemoryEntry], int]:
        statement = select(WorkspaceMemoryEntry).where(
            WorkspaceMemoryEntry.workspace_id == workspace_id,
            WorkspaceMemoryEntry.memory_layer == "semantic",
            WorkspaceMemoryEntry.source_type == "semantic_memory",
        )
        if not include_archived:
            statement = statement.where(WorkspaceMemoryEntry.status == "active")
        if scope_type is not None:
            normalized_scope = scope_type.strip().lower()
            if normalized_scope not in SEMANTIC_SCOPE_TYPES:
                raise ValueError(f"Unsupported semantic memory scope: {normalized_scope}")
            statement = statement.where(WorkspaceMemoryEntry.scope_type == normalized_scope)
        if scope_id is not None:
            statement = statement.where(WorkspaceMemoryEntry.scope_id == str(scope_id))
        total = int(
            self._session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        entries = self._session.scalars(
            statement.order_by(
                WorkspaceMemoryEntry.importance.desc(),
                WorkspaceMemoryEntry.updated_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        ).all()
        return list(entries), total

    def _validate_scope(self, command: SemanticMemoryUpsert) -> tuple[str, str]:
        scope_type = command.scope_type.strip().lower()
        if scope_type not in SEMANTIC_SCOPE_TYPES:
            raise ValueError(f"Unsupported semantic memory scope: {scope_type}")
        if scope_type == "workspace":
            if command.scope_id != command.workspace_id:
                raise ValueError("Workspace memory scope must reference the current workspace")
        elif scope_type == "team":
            team = self._session.get(AgentTeam, command.scope_id)
            if team is None or team.workspace_id != command.workspace_id:
                raise ValueError("Semantic memory team scope was not found in the workspace")
        else:
            profile = self._session.get(AgentProfile, command.scope_id)
            if profile is None or profile.workspace_id != command.workspace_id:
                raise ValueError("Semantic memory agent scope was not found in the workspace")
        self._validate_actor_scope(command)
        return scope_type, str(command.scope_id)

    def _validate_actor_scope(self, command: SemanticMemoryUpsert) -> None:
        if command.changed_by_agent_profile_id is not None:
            profile = self._session.get(AgentProfile, command.changed_by_agent_profile_id)
            if profile is None or profile.workspace_id != command.workspace_id:
                raise ValueError("Semantic memory actor agent is outside the workspace")
        if command.changed_by_agent_run_id is not None:
            run = self._session.get(AgentRun, command.changed_by_agent_run_id)
            if run is None or run.workspace_id != command.workspace_id:
                raise ValueError("Semantic memory actor run is outside the workspace")

    def _record_version(
        self,
        entry: WorkspaceMemoryEntry,
        command: SemanticMemoryUpsert,
    ) -> None:
        self._session.add(
            WorkspaceMemoryVersion(
                workspace_id=entry.workspace_id,
                memory_entry_id=entry.id,
                revision=entry.revision,
                snapshot=_entry_snapshot(entry),
                content_fingerprint=entry.content_fingerprint,
                changed_by_user_id=command.changed_by_user_id,
                changed_by_agent_profile_id=command.changed_by_agent_profile_id,
                changed_by_agent_run_id=command.changed_by_agent_run_id,
                change_reason=redact_text_fragments(command.change_reason or "") or None,
            )
        )
        self._session.flush()


def _entry_snapshot(entry: WorkspaceMemoryEntry) -> dict[str, object]:
    return {
        "memory_layer": entry.memory_layer,
        "scope_type": entry.scope_type,
        "scope_id": entry.scope_id,
        "memory_key": entry.memory_key,
        "knowledge_type": entry.entry_type.removeprefix("semantic_"),
        "title": entry.title,
        "content": entry.content,
        "tags": entry.tags,
        "importance": entry.importance,
        "metadata": entry.memory_metadata,
        "status": entry.status,
        "archived_at": entry.archived_at.isoformat() if entry.archived_at else None,
    }


def _head_values(entry: WorkspaceMemoryEntry) -> dict[str, object]:
    return _semantic_snapshot_values(
        knowledge_type=entry.entry_type.removeprefix("semantic_"),
        title=entry.title,
        content=entry.content,
        tags=entry.tags,
        importance=entry.importance,
        metadata=entry.memory_metadata,
        status=entry.status,
    )


def _semantic_snapshot_values(
    *,
    knowledge_type: str,
    title: str,
    content: str,
    tags: list[str],
    importance: int,
    metadata: dict[str, object],
    status: str,
) -> dict[str, object]:
    return {
        "knowledge_type": knowledge_type,
        "title": title,
        "content": content,
        "tags": tags,
        "importance": importance,
        "metadata": metadata,
        "status": status,
    }


def _required_text(value: str, limit: int, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"Semantic memory {field} is required")
    if len(normalized) > limit:
        raise ValueError(f"Semantic memory {field} exceeds {limit} characters")
    return normalized


def _tags(values: list[str]) -> list[str]:
    normalized = sorted({tag.strip().lower() for tag in values if tag.strip()})
    if len(normalized) > 32 or any(len(tag) > 80 for tag in normalized):
        raise ValueError("Semantic memory tags exceed the supported limits")
    return normalized


def _importance(value: int) -> int:
    if isinstance(value, bool) or not 0 <= value <= 100:
        raise ValueError("Semantic memory importance must be between 0 and 100")
    return value
