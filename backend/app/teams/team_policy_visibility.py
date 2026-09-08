from __future__ import annotations

from backend.app.security.redaction import redact_sensitive_payload

INTERNAL_POLICY_KEYS = {"team_runtime", "team_runtime_thread_id"}


def visible_task_policy(value: object, *, redact: bool = False) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    visible_policy = {key: item for key, item in value.items() if key not in INTERNAL_POLICY_KEYS}
    if redact:
        return redact_sensitive_payload(visible_policy, text_mode="fragments")
    return visible_policy
