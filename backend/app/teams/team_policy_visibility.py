from __future__ import annotations

from backend.app.teams.team_context_redaction import redact_context_value

INTERNAL_POLICY_KEYS = {"team_runtime", "team_runtime_thread_id"}


def visible_task_policy(value: object, *, redact: bool = False) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    visible_policy = {
        key: item for key, item in value.items() if key not in INTERNAL_POLICY_KEYS
    }
    if redact:
        return redact_context_value(visible_policy)
    return visible_policy
