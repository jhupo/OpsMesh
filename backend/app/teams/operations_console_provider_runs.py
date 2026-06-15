from __future__ import annotations

from backend.app.teams.operations_console_utils import _dict


def _run_model_provider_snapshot(input_payload: object) -> dict[str, object]:
    payload = _dict(input_payload)
    authorization_snapshot = _dict(payload.get("authorization_snapshot"))
    snapshot = _dict(authorization_snapshot.get("model_provider"))
    if snapshot:
        return snapshot
    return _dict(payload.get("model_provider"))
