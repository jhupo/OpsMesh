from __future__ import annotations

from backend.app.agent_runtime.session_management import PersistentSessionSummary
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.teams.models import AgentTeam
from backend.app.teams.operations_console_utils import (
    _redacted_dict_or_none,
    _visible_task_policy,
)


def _team_payload(team: AgentTeam) -> dict[str, object]:
    return {
        "id": team.id,
        "workspace_id": team.workspace_id,
        "name": team.name,
        "team_type": team.team_type,
        "description": team.description,
        "status": team.status,
        "manager_agent_profile_id": team.manager_agent_profile_id,
        "runtime_space_id": team.runtime_space_id,
        "coordination_rules": dict(team.coordination_rules),
        "default_task_policy": _visible_task_policy(team.default_task_policy),
    }


def _command_center_payload(command_center: dict[str, object] | None) -> dict[str, object]:
    if command_center is None:
        return {
            "summary": {},
            "runtime": {},
            "provider_readiness": {},
            "operating_policy": {},
            "memory_summary": {},
            "overview": None,
            "queues": None,
            "action_plan": [],
        }
    return {
        "workspace_id": command_center.get("workspace_id"),
        "team_id": command_center.get("team_id"),
        "generated_at": command_center.get("generated_at"),
        "summary": redact_sensitive_payload(dict(command_center.get("summary") or {})),
        "runtime": redact_sensitive_payload(dict(command_center.get("runtime") or {})),
        "provider_readiness": redact_sensitive_payload(
            dict(command_center.get("provider_readiness") or {})
        ),
        "operating_policy": redact_sensitive_payload(
            dict(command_center.get("operating_policy") or {})
        ),
        "memory_summary": redact_sensitive_payload(
            dict(command_center.get("memory_summary") or {})
        ),
        "overview": redact_sensitive_payload(dict(command_center.get("overview") or {})),
        "queues": redact_sensitive_payload(dict(command_center.get("queues") or {})),
        "action_plan": [
            redact_sensitive_payload(item)
            for item in command_center.get("action_plan") or []
            if isinstance(item, dict)
        ],
    }


def _session_payload(summary: PersistentSessionSummary | None) -> dict[str, object] | None:
    if summary is None:
        return None
    return {
        "id": summary.id,
        "session_key": summary.session_key,
        "scope_type": summary.scope_type,
        "scope_id": summary.scope_id,
        "status": summary.status,
        "agent_profile_id": summary.agent_profile_id,
        "agent_team_id": summary.agent_team_id,
        "item_count": summary.item_count,
        "openai_conversation_id": summary.openai_conversation_id,
        "latest_item_metadata": _redacted_dict_or_none(summary.latest_item_metadata),
        "updated_at": summary.updated_at,
    }


