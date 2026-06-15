from __future__ import annotations

from typing import Any

from backend.app.security.redaction import redact_sensitive_payload
from backend.app.tasks.models import TaskMessage


def risk_flags_from_payload(payload: dict[str, object]) -> list[dict[str, object]]:
    risks: list[dict[str, object]] = []
    raw_risks = payload.get("risks")
    if isinstance(raw_risks, list):
        risks.extend(item for item in raw_risks if isinstance(item, dict))

    risk_level = payload.get("risk_level")
    if isinstance(risk_level, str) and risk_level:
        risks.append(
            {
                "title": str(payload.get("title") or "Risk flagged"),
                "severity": risk_level,
                "reason": payload.get("reason") or payload.get("summary"),
            }
        )

    decision = payload.get("decision")
    reasons = payload.get("reasons")
    if decision == "request_revision":
        risks.append(
            {
                "title": "Revision requested",
                "severity": "attention",
                "reason": reasons if isinstance(reasons, list) else payload.get("summary"),
            }
        )
    return risks


def count_items(value: object) -> int:
    if value in (None, "", [], {}):
        return 0
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if isinstance(value, dict):
        for key in ("items", "results", "records", "entries"):
            nested = value.get(key)
            if isinstance(nested, (list, tuple, set)):
                return len(nested)
        count = value.get("count")
        if isinstance(count, int):
            return max(count, 0)
        return len(value)
    if isinstance(value, str):
        return 1 if value.strip() else 0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return 1 if value > 0 else 0
    return 1 if value else 0


def present(value: object) -> bool:
    return count_items(value) > 0


def int_value(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    if isinstance(value, str):
        try:
            return max(int(value.strip()), 0)
        except ValueError:
            return 0
    if isinstance(value, dict):
        for key in ("count", "value", "total"):
            parsed = int_value(value.get(key))
            if parsed:
                return parsed
    return 0


def safe_message_payload(payload: dict[str, object]) -> dict[str, object]:
    return redact_sensitive_payload(payload)


def status_from_message_type(message_type: str) -> str:
    if message_type.endswith(".failed") or message_type.endswith(".blocked"):
        return "attention"
    if message_type.endswith(".completed") or message_type == "pm.acceptance_decision":
        return "completed"
    return "recorded"


def str_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None


def review_event_status(message: TaskMessage) -> str:
    return str(message.payload.get("decision") or message.payload.get("status") or "recorded")
