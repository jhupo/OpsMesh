from dataclasses import dataclass, field

from backend.app.orchestration.run_result_payloads import json_object_from_text


@dataclass(frozen=True)
class TaskProgressUpdate:
    generic_state: dict[str, object] = field(default_factory=dict)
    domain_state: dict[str, object] = field(default_factory=dict)
    task_input: dict[str, object] = field(default_factory=dict)
    progress: object | None = None
    summary: str | None = None


def task_progress_from_output(final_output: str) -> TaskProgressUpdate | None:
    payload = json_object_from_text(final_output)
    if payload is None:
        return None
    progress_payload = payload.get("task_progress")
    if isinstance(progress_payload, dict):
        payload = progress_payload

    generic_state = _progress_state_dict(payload.get("generic_state") or payload.get("state"))
    domain_state = _progress_state_dict(
        payload.get("domain_state") or payload.get("domain_progress")
    )
    task_input = _progress_state_dict(payload.get("input") or payload.get("task_input"))
    progress = payload.get("progress")
    summary = payload.get("summary")
    if not isinstance(summary, str):
        summary = payload.get("progress_summary")
    if not isinstance(summary, str):
        summary = None

    if not generic_state and not domain_state and not task_input:
        return None
    return TaskProgressUpdate(
        generic_state=generic_state,
        domain_state=domain_state,
        task_input=task_input,
        progress=progress if isinstance(progress, str | int | float | bool) else None,
        summary=summary,
    )


def deep_merge_dict(
    existing: dict[str, object],
    update: dict[str, object],
) -> dict[str, object]:
    merged = dict(existing) if isinstance(existing, dict) else {}
    for key, value in update.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge_dict(current, value)
        else:
            merged[key] = value
    return merged


def _progress_state_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _json_safe_progress_value(item)
        for key, item in value.items()
        if isinstance(key, str) and item is not None
    }


def _json_safe_progress_value(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_json_safe_progress_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _json_safe_progress_value(item)
            for key, item in value.items()
            if isinstance(key, str)
        }
    return str(value)
