from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from backend.app.agents.models import AgentProfile
from backend.app.model_providers.model_api import require_known_model_api

AGENT_PROFILE_FIELDS = (
    "name",
    "role",
    "description",
    "instructions",
    "model",
    "model_provider_credential_id",
    "model_settings",
    "capabilities",
    "skills",
    "tool_policy",
    "runtime_policy",
    "memory_policy",
    "approval_policy",
)

AGENT_PROFILE_REVIEW_FIELDS = {
    "name",
    "role",
    "instructions",
    "capabilities",
    "skills",
    "tool_policy",
    "runtime_policy",
    "approval_policy",
    "model",
    "model_provider_credential_id",
    "model_settings",
}

JSON_PROFILE_FIELDS = (
    "model_settings",
    "capabilities",
    "skills",
    "tool_policy",
    "runtime_policy",
    "memory_policy",
    "approval_policy",
)

NON_NULL_PROFILE_FIELDS = (
    "name",
    "role",
    "description",
    "instructions",
    "model",
)

CREATE_DEFAULTS: dict[str, object] = {
    "description": "",
    "instructions": "",
    "model": "gpt-4.1",
    "model_provider_credential_id": None,
    "model_settings": {},
    "capabilities": {},
    "skills": {},
    "tool_policy": {},
    "runtime_policy": {},
    "memory_policy": {},
    "approval_policy": {},
}


def normalize_create_payload(data: object, fields: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(CREATE_DEFAULTS)
    payload = payload_dict(data)
    payload.update(fields)
    payload.pop("status", None)
    merge_top_level_model_api(payload)
    unknown = set(payload) - set(AGENT_PROFILE_FIELDS)
    if unknown:
        raise ValueError(f"Unsupported agent profile fields: {', '.join(sorted(unknown))}")
    missing_required = [field for field in ("name", "role") if not payload.get(field)]
    if missing_required:
        raise ValueError(f"Missing agent profile fields: {', '.join(missing_required)}")
    reject_null_profile_fields(payload)
    values.update(payload)
    return {field: copy_json_value(field, values[field]) for field in AGENT_PROFILE_FIELDS}


def normalize_update_payload(
    data: object,
    fields: Mapping[str, Any],
    *,
    current_model_settings: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    payload = payload_dict(data)
    payload.update(fields)
    merge_top_level_model_api(payload, current_model_settings=current_model_settings)
    unknown = set(payload) - (set(AGENT_PROFILE_FIELDS) | {"status"})
    if unknown:
        raise ValueError(f"Unsupported agent profile fields: {', '.join(sorted(unknown))}")
    reject_null_profile_fields(payload)
    for required_text_field in ("name", "role"):
        if required_text_field in payload and not payload[required_text_field]:
            raise ValueError(f"Agent profile {required_text_field} is required")
    return {field: copy_json_value(field, value) for field, value in payload.items()}


def payload_dict(data: object) -> dict[str, Any]:
    if data is None:
        return {}
    if isinstance(data, Mapping):
        return dict(data)
    model_dump = getattr(data, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(exclude_unset=True))
    raise ValueError("Agent profile payload must be a mapping")


def reject_null_profile_fields(payload: Mapping[str, Any]) -> None:
    null_fields = [
        field for field in NON_NULL_PROFILE_FIELDS if field in payload and payload[field] is None
    ]
    if null_fields:
        raise ValueError(f"Agent profile fields cannot be null: {', '.join(null_fields)}")


def rollback_reason(data: object) -> str | None:
    payload = payload_dict(data)
    reason = payload.get("reason")
    if reason is None:
        return None
    if not isinstance(reason, str):
        raise ValueError("Rollback reason must be a string")
    return reason


def profile_snapshot(profile: AgentProfile) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "id": str(profile.id),
        "workspace_id": str(profile.workspace_id),
        "version": profile.version,
        "status": profile.status,
        "archived_at": profile.archived_at.isoformat() if profile.archived_at else None,
    }
    for field in AGENT_PROFILE_FIELDS:
        value = getattr(profile, field)
        if isinstance(value, UUID):
            snapshot[field] = str(value)
        elif field in JSON_PROFILE_FIELDS:
            snapshot[field] = dict(value or {})
        else:
            snapshot[field] = value
    return snapshot


def profile_audit_state(snapshot: Mapping[str, object]) -> dict[str, object]:
    return {
        "status": snapshot.get("status"),
        "version": snapshot.get("version"),
        "model": snapshot.get("model"),
        "model_provider_credential_id": snapshot.get("model_provider_credential_id"),
    }


def merge_top_level_model_api(
    payload: dict[str, Any],
    *,
    current_model_settings: Mapping[str, object] | None = None,
) -> None:
    if "model_api" not in payload:
        return
    raw_model_api = payload.pop("model_api")
    raw_settings = payload.get("model_settings")
    if raw_settings is None:
        raw_settings = current_model_settings
    if raw_settings is not None and not isinstance(raw_settings, Mapping):
        raise ValueError("Agent profile model_settings must be an object")
    settings = dict(raw_settings or {})
    model_api = require_known_model_api(raw_model_api)
    if model_api is None:
        settings.pop("model_api", None)
    else:
        settings["model_api"] = model_api
    payload["model_settings"] = settings


def copy_json_value(field: str, value: Any) -> Any:
    if field in JSON_PROFILE_FIELDS:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError(f"Agent profile {field} must be an object")
        return dict(value)
    if field == "model_provider_credential_id":
        return uuid_or_none(value)
    return value


def uuid_or_none(value: object) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise ValueError("Expected UUID value")


def datetime_or_none(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("Expected datetime value")
