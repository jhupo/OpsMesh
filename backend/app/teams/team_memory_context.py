from __future__ import annotations

from datetime import datetime
from uuid import UUID

from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.teams.models import AgentTeam
from backend.app.teams.team_context_redaction import (
    redact_context_value,
    redact_secret_like_text,
)

TEAM_MEMORY_SCOPES = {"team"}
SHARED_MEMORY_SCOPES = {"workspace", "company", "organization", "shared"}
MEMORY_SUMMARY_LIMIT = 8
MEMORY_SNIPPET_LENGTH = 280
TEAM_SOURCE_TYPE = "agent_team"
TEAM_METADATA_KEY = "agent_team_id"


def memory_visibility_scopes() -> list[str]:
    return sorted(TEAM_MEMORY_SCOPES | SHARED_MEMORY_SCOPES)


def memory_summary_payload(
    *,
    team: AgentTeam,
    entries: list[WorkspaceMemoryEntry],
) -> dict[str, object]:
    visible_entries = _sort_memory_entries(
        [entry for entry in entries if _is_visible_to_team(entry, team)]
    )
    team_entries = [entry for entry in visible_entries if _is_team_memory(entry, team.id)]
    shared_entries = [entry for entry in visible_entries if not _is_team_memory(entry, team.id)]

    return {
        "workspace_id": team.workspace_id,
        "team_id": team.id,
        "active_entry_count": len(visible_entries),
        "team_entry_count": len(team_entries),
        "shared_entry_count": len(shared_entries),
        "scope_counts": _memory_scope_counts(visible_entries),
        "last_updated_at": _last_updated_at(visible_entries),
        "tags": redact_context_value(_top_tags(visible_entries)),
        "entries": [
            _memory_entry_payload(entry) for entry in visible_entries[:MEMORY_SUMMARY_LIMIT]
        ],
    }


def _is_visible_to_team(entry: WorkspaceMemoryEntry, team: AgentTeam) -> bool:
    if entry.visibility_scope in SHARED_MEMORY_SCOPES:
        return True
    if entry.visibility_scope in TEAM_MEMORY_SCOPES:
        return _is_team_memory(entry, team.id)
    return False


def _is_team_memory(entry: WorkspaceMemoryEntry, team_id: UUID) -> bool:
    metadata = entry.memory_metadata if isinstance(entry.memory_metadata, dict) else {}
    team_id_text = str(team_id)
    if entry.source_type == TEAM_SOURCE_TYPE and entry.source_id == team_id_text:
        return True
    return _metadata_uuid_text(metadata.get(TEAM_METADATA_KEY)) == team_id_text


def _memory_entry_payload(entry: WorkspaceMemoryEntry) -> dict[str, object]:
    return {
        "id": entry.id,
        "title": redact_secret_like_text(entry.title),
        "snippet": _snippet(entry.content),
        "entry_type": entry.entry_type,
        "visibility_scope": entry.visibility_scope,
        "importance": entry.importance,
        "tags": redact_context_value(entry.tags),
        "source_type": entry.source_type,
        "source_id": redact_context_value(entry.source_id),
        "updated_at": entry.updated_at,
        "metadata": redact_context_value(entry.memory_metadata),
    }


def _snippet(value: str) -> str:
    collapsed = redact_secret_like_text(" ".join(value.split()))
    if len(collapsed) <= MEMORY_SNIPPET_LENGTH:
        return collapsed
    return f"{collapsed[:MEMORY_SNIPPET_LENGTH].rstrip()}..."


def _last_updated_at(entries: list[WorkspaceMemoryEntry]) -> datetime | None:
    values = [entry.updated_at for entry in entries if entry.updated_at is not None]
    return max(values) if values else None


def _top_tags(entries: list[WorkspaceMemoryEntry]) -> list[str]:
    counts: dict[str, int] = {}
    for entry in entries:
        for tag in entry.tags:
            counts[tag] = counts.get(tag, 0) + 1
    return [tag for tag, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:20]]


def _memory_scope_counts(entries: list[WorkspaceMemoryEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        scope = entry.visibility_scope
        counts[scope] = counts.get(scope, 0) + 1
    return dict(sorted(counts.items()))


def _sort_memory_entries(
    entries: list[WorkspaceMemoryEntry],
) -> list[WorkspaceMemoryEntry]:
    return sorted(entries, key=_memory_sort_key, reverse=True)


def _memory_sort_key(entry: WorkspaceMemoryEntry) -> tuple[int, float, float]:
    return (
        entry.importance or 0,
        _datetime_timestamp(entry.updated_at),
        _datetime_timestamp(entry.created_at),
    )


def _datetime_timestamp(value: datetime | None) -> float:
    if value is None:
        return 0.0
    return value.timestamp()


def _metadata_uuid_text(value: object) -> str | None:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        return value
    return None
