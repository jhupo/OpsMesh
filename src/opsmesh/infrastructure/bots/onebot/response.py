"""The HTTP and WebSocket transports share OneBot's structured API response rules."""

from typing import Any

from opsmesh.framework.errors import OpsMeshError


def api_result(payload: Any) -> Any:
    if not isinstance(payload, dict):
        raise OpsMeshError("DELIVERY_UNKNOWN", "OneBot response is not an object")
    if payload.get("status") == "ok" and payload.get("retcode") == 0:
        return payload.get("data")
    if payload.get("status") == "async":
        raise OpsMeshError("DELIVERY_UNKNOWN", "OneBot accepted an asynchronous operation")
    if (
        payload.get("status") != "failed"
        or not isinstance(payload.get("retcode"), int)
        or payload["retcode"] in (0, 1)
    ):
        raise OpsMeshError("DELIVERY_UNKNOWN", "OneBot returned an inconsistent API response")
    raise OpsMeshError(
        "BOT_API_FAILED",
        "OneBot rejected the operation",
        retryable=True,
        details={"retcode": payload.get("retcode")},
    )
