from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager

from backend.app.agents.models import AgentProfile
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.security.redaction import (
    REDACTED_VALUE,
    is_sensitive_payload_key,
)
from backend.app.teams.models import AgentTeam, AgentTeamMember

TEAM_MEMORY_SCOPES = {"team", "agent_team"}
SHARED_MEMORY_SCOPES = {"workspace", "company", "organization", "shared"}
MEMORY_SUMMARY_LIMIT = 8
MEMORY_SNIPPET_LENGTH = 280
INTERNAL_POLICY_KEYS = {"team_runtime", "team_runtime_thread_id"}
SECRET_LIKE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\b(?:ccut|gh[opsu]|github_pat)_[A-Za-z0-9_=-]{8,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{6,}\b"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|token|secret|password)\s*[:=]\s*"
        r"['\"]?[^\s,'\";}]+['\"]?"
    ),
)


class TeamOperatingContextService:
    """Build the persistent operating policy and long-term memory context for a team."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_context(self, *, workspace_id: UUID, team_id: UUID) -> dict[str, object] | None:
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )
        if team is None:
            return None
        members = list(
            self._session.scalars(
                select(AgentTeamMember)
                .where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id == team_id,
                )
                .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            )
        )
        return {
            "operating_policy": self.operating_policy(team=team, members=members),
            "memory_summary": self.memory_summary(team=team),
        }

    def operating_policy(
        self,
        *,
        team: AgentTeam,
        members: list[AgentTeamMember] | None = None,
    ) -> dict[str, object]:
        resolved_members = (
            members
            if members is not None and all(member.agent_profile is not None for member in members)
            else self._team_members(team)
        )
        active_members = [member for member in resolved_members if member.status == "active"]
        accepting_members = [
            member for member in active_members if member.accepts_tasks
        ]
        roles = sorted({member.team_role for member in active_members})
        departments = sorted(
            {
                member.department
                for member in active_members
                if member.department is not None
            }
        )
        max_concurrent_tasks = sum(
            member.max_concurrent_tasks for member in accepting_members
        )
        return {
            "workspace_id": team.workspace_id,
            "team_id": team.id,
            "team_name": _redact_secret_like_text(team.name),
            "team_type": team.team_type,
            "description": _redact_secret_like_text(team.description),
            "status": team.status,
            "manager_agent_profile_id": team.manager_agent_profile_id,
            "runtime_space_id": team.runtime_space_id,
            "runtime_space": self._runtime_space_summary(team),
            "coordination_rules": _redact_value(team.coordination_rules or {}),
            "default_task_policy": _visible_task_policy(team.default_task_policy),
            "staffing": {
                "total_member_count": len(resolved_members),
                "active_member_count": len(active_members),
                "accepting_member_count": len(accepting_members),
                "required_member_count": sum(
                    1 for member in active_members if member.is_required
                ),
                "inactive_member_count": sum(
                    1 for member in resolved_members if member.status != "active"
                ),
                "non_accepting_member_count": len(active_members)
                - len(accepting_members),
                "max_concurrent_tasks": max_concurrent_tasks,
                "roles": roles,
                "departments": departments,
                "member_limits": [
                    {
                        "member_id": member.id,
                        "agent_profile_id": member.agent_profile_id,
                        "team_role": member.team_role,
                        "status": member.status,
                        "accepts_tasks": member.accepts_tasks,
                        "is_required": member.is_required,
                        "max_concurrent_tasks": member.max_concurrent_tasks,
                    }
                    for member in resolved_members
                ],
            },
            "members": [_member_policy_summary(member) for member in active_members],
        }

    def memory_summary(self, *, team: AgentTeam) -> dict[str, object]:
        entries = [
            entry
            for entry in self._workspace_scoped_memory_entries(team.workspace_id)
            if _is_visible_to_team(entry, team)
        ]
        entries = _sort_memory_entries(entries)
        team_entries = [entry for entry in entries if _is_team_memory(entry, team.id)]
        shared_entries = [entry for entry in entries if not _is_team_memory(entry, team.id)]
        top_entries = entries[:MEMORY_SUMMARY_LIMIT]
        return {
            "workspace_id": team.workspace_id,
            "team_id": team.id,
            "active_entry_count": len(entries),
            "team_entry_count": len(team_entries),
            "shared_entry_count": len(shared_entries),
            "scope_counts": _memory_scope_counts(entries),
            "last_updated_at": _last_updated_at(entries),
            "tags": _redact_value(_top_tags(entries)),
            "entries": [_memory_entry_payload(entry) for entry in top_entries],
        }

    def _team_members(self, team: AgentTeam) -> list[AgentTeamMember]:
        return list(
            self._session.scalars(
                select(AgentTeamMember)
                .join(AgentProfile, AgentProfile.id == AgentTeamMember.agent_profile_id)
                .options(contains_eager(AgentTeamMember.agent_profile))
                .where(
                    AgentTeamMember.workspace_id == team.workspace_id,
                    AgentTeamMember.agent_team_id == team.id,
                    AgentProfile.workspace_id == team.workspace_id,
                )
                .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            )
        )

    def _workspace_scoped_memory_entries(self, workspace_id: UUID) -> list[WorkspaceMemoryEntry]:
        return list(
            self._session.scalars(
                select(WorkspaceMemoryEntry)
                .where(
                    WorkspaceMemoryEntry.workspace_id == workspace_id,
                    WorkspaceMemoryEntry.status == "active",
                    WorkspaceMemoryEntry.visibility_scope.in_(
                        sorted(TEAM_MEMORY_SCOPES | SHARED_MEMORY_SCOPES)
                    ),
                )
            )
        )

    def _runtime_space_summary(self, team: AgentTeam) -> dict[str, object] | None:
        if team.runtime_space_id is None:
            return None
        runtime_space = self._session.scalar(
            select(RuntimeSpace).where(
                RuntimeSpace.workspace_id == team.workspace_id,
                RuntimeSpace.id == team.runtime_space_id,
            )
        )
        if runtime_space is None:
            return None
        return {
            "id": runtime_space.id,
            "name": _redact_secret_like_text(runtime_space.name),
            "scope": runtime_space.scope,
            "status": runtime_space.status,
            "default_runtime_template_id": runtime_space.default_runtime_template_id,
            "policy": _redact_value(runtime_space.policy),
            "network_policy": _redact_value(runtime_space.network_policy),
            "storage_policy": _redact_value(runtime_space.storage_policy),
            "cleanup_policy": _redact_value(runtime_space.cleanup_policy),
        }


def _is_visible_to_team(entry: WorkspaceMemoryEntry, team: AgentTeam) -> bool:
    if entry.visibility_scope in SHARED_MEMORY_SCOPES:
        return True
    if entry.visibility_scope in TEAM_MEMORY_SCOPES:
        return _is_team_memory(entry, team.id)
    return False


def _is_team_memory(entry: WorkspaceMemoryEntry, team_id: UUID) -> bool:
    metadata = entry.memory_metadata if isinstance(entry.memory_metadata, dict) else {}
    candidate_values = {
        _metadata_value(metadata, "team_id"),
        _metadata_value(metadata, "teamId"),
        _metadata_value(metadata, "agent_team_id"),
        _metadata_value(metadata, "agentTeamId"),
        _metadata_value(metadata, "scope_id"),
        _metadata_value(metadata, "scopeId"),
        entry.source_id if entry.source_type in {"team", "agent_team"} else None,
    }
    team_id_text = str(team_id)
    if any(str(value) == team_id_text for value in candidate_values if value is not None):
        return True
    team_tag_values = {f"team:{team_id_text}", f"agent_team:{team_id_text}"}
    return any(tag in team_tag_values for tag in entry.tags)


def _metadata_value(metadata: dict[str, object], key: str) -> object:
    return metadata.get(key)


def _memory_entry_payload(entry: WorkspaceMemoryEntry) -> dict[str, object]:
    return {
        "id": entry.id,
        "title": _redact_secret_like_text(entry.title),
        "snippet": _snippet(entry.content),
        "entry_type": entry.entry_type,
        "visibility_scope": entry.visibility_scope,
        "importance": entry.importance,
        "tags": _redact_value(entry.tags),
        "source_type": entry.source_type,
        "source_id": _redact_value(entry.source_id),
        "updated_at": entry.updated_at,
        "metadata": _redact_value(entry.memory_metadata),
    }


def _visible_task_policy(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return _redact_value(
        {key: item for key, item in value.items() if key not in INTERNAL_POLICY_KEYS}
    )


def _snippet(value: str) -> str:
    collapsed = _redact_secret_like_text(" ".join(value.split()))
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
    return [
        tag
        for tag, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:20]
    ]


def _memory_scope_counts(entries: list[WorkspaceMemoryEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        scope = entry.visibility_scope
        counts[scope] = counts.get(scope, 0) + 1
    return dict(sorted(counts.items()))


def _member_policy_summary(member: AgentTeamMember) -> dict[str, object]:
    agent = member.agent_profile
    return {
        "member_id": member.id,
        "agent_profile_id": member.agent_profile_id,
        "agent_name": _redact_secret_like_text(agent.name),
        "agent_role": agent.role,
        "team_role": member.team_role,
        "department": _redact_value(member.department),
        "position_title": _redact_value(member.position_title),
        "reports_to_member_id": member.reports_to_member_id,
        "accepts_tasks": member.accepts_tasks,
        "is_required": member.is_required,
        "max_concurrent_tasks": member.max_concurrent_tasks,
        "order_index": member.order_index,
        "agent_policy": {
            "model": _redact_secret_like_text(agent.model),
            "model_settings": _redact_value(agent.model_settings),
            "tool_policy": _redact_value(agent.tool_policy),
            "runtime_preferences": _redact_value(
                getattr(agent, "runtime_preferences", agent.runtime_policy)
            ),
            "runtime_policy": _redact_value(agent.runtime_policy),
            "approval_policy": _redact_value(agent.approval_policy),
            "memory_policy": _redact_value(agent.memory_policy),
            "capabilities": _redact_value(agent.capabilities),
            "skills": _redact_value(agent.skills),
        },
    }


def _sort_memory_entries(
    entries: list[WorkspaceMemoryEntry],
) -> list[WorkspaceMemoryEntry]:
    return sorted(
        entries,
        key=_memory_sort_key,
        reverse=True,
    )


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


def _redact_value(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if is_sensitive_payload_key(key_text):
                redacted[key_text] = REDACTED_VALUE
                continue
            redacted[key_text] = _redact_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_secret_like_text(value)
    return value


def _redact_secret_like_text(value: str) -> str:
    redacted = value
    for pattern in SECRET_LIKE_PATTERNS:
        redacted = pattern.sub(REDACTED_VALUE, redacted)
    return redacted
