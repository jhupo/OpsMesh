from __future__ import annotations

from uuid import UUID

from backend.app.reviews.constants import DEFAULT_RESOURCE_REVIEW_MODEL

_HIGH_RISK_LEVELS = {"high", "critical"}
_REVIEW_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_HIGH_RISK_TERMS = {
    "bypass",
    "credential",
    "delete",
    "payment",
    "production",
    "root",
    "secret",
    "shell",
    "sudo",
    "token",
}
_DANGEROUS_WORDS = {
    "admin",
    "approve",
    "approval",
    "bypass",
    "credential",
    "delete",
    "deploy",
    "exec",
    "filesystem",
    "network",
    "payment",
    "production",
    "root",
    "secret",
    "shell",
    "sudo",
    "token",
    "write",
}
_PUBLIC_SCOPES = {"public", "marketplace"}

_PRIVATE_REVIEW_DEFAULTS = {
    "agent_profile": False,
    "skill": False,
    "mcp_server": False,
    "mcp_tool_allowlist": False,
    "mcp_credential_reference": False,
    "plugin": False,
}


def _max_risk(left: str, right: str) -> str:
    left_risk = _normalize_risk(left)
    right_risk = _normalize_risk(right)
    return (
        left_risk if _REVIEW_RISK_ORDER[left_risk] >= _REVIEW_RISK_ORDER[right_risk] else right_risk
    )


def _walk_mapping(value: dict[str, object], prefix: str) -> list[tuple[str, object]]:
    rows: list[tuple[str, object]] = []
    for key, item in value.items():
        path = f"{prefix}.{key}"
        rows.append((path, item))
        if isinstance(item, dict):
            rows.extend(_walk_mapping(item, path))
        elif isinstance(item, list):
            for index, child in enumerate(item[:50]):
                child_path = f"{path}[{index}]"
                rows.append((child_path, child))
                if isinstance(child, dict):
                    rows.extend(_walk_mapping(child, child_path))
    return rows


def _normalize_risk(value: object) -> str:
    raw = str(value or "low").lower().strip()
    return raw if raw in _REVIEW_RISK_ORDER else "low"


def _review_model(value: object) -> str:
    model = value.strip() if isinstance(value, str) else ""
    return model or DEFAULT_RESOURCE_REVIEW_MODEL


def _review_timeout_seconds(value: object) -> float:
    if isinstance(value, bool):
        return 20.0
    if isinstance(value, int | float) and 1 <= value <= 120:
        return float(value)
    return 20.0


def _uuid_or_none(value: object) -> UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise ValueError("Review model provider credential id must be a UUID")


def _policy_mode(policy: dict[str, object]) -> str:
    return str(policy.get("mode") or policy.get("tool_access") or "").lower().strip()


def _path_has_dangerous_term(path: str) -> bool:
    lowered = path.lower()
    return any(term in lowered for term in _DANGEROUS_WORDS)


def _list_from_manifest(manifest: dict[str, object], key: str) -> list[str]:
    value = manifest.get(key)
    if not isinstance(value, list):
        value = manifest.get("tools")
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _connection_has_external_url(connection: dict[str, object]) -> bool:
    for _, item in _walk_mapping(connection, "connection"):
        if isinstance(item, str) and (
            item.startswith("http://")
            or item.startswith("https://")
            or item.startswith("ws://")
            or item.startswith("wss://")
        ):
            return True
    return False


def _is_public_visibility(value: object) -> bool:
    return str(value or "private").lower().strip() in _PUBLIC_SCOPES


def _has_sensitive_keys(value: dict[str, object]) -> bool:
    for key, item in value.items():
        if isinstance(key, str) and any(term in key.lower() for term in _DANGEROUS_WORDS):
            return True
        if isinstance(item, dict) and _has_sensitive_keys(item):
            return True
    return False
