"""Safe, inspectable errors from platform operations."""

import re

import httpx
from pydantic import JsonValue

_MAX_RESPONSE_BYTES = 1024 * 1024


class OperationError(Exception):
    """A vendor operation failed without exposing its response body."""

    def __init__(
        self, status: int, code: str = "unknown", request_id: str | None = None
    ):
        """Expose safe vendor identifiers for caller-owned error policy."""
        self.status = status
        self.code = code
        self.request_id = request_id
        self.rejected = (
            not 200 <= status < 300
            and status != 0
            or code
            not in {
                "unknown",
                "transport_error",
                "invalid_json",
                "invalid_response",
                "response_too_large",
            }
        )
        super().__init__(f"Platform operation failed ({status}, {code})")


class UnsupportedOperation(ValueError):
    """The adapter does not implement this platform operation."""


class OperationPermissionDenied(OperationError):
    """The platform rejected the account or its application permissions."""


class OperationRateLimited(OperationError):
    """The platform rejected the request because of a rate limit."""


def operation_response(response: httpx.Response) -> JsonValue:
    """Read a bounded response and preserve safe failure identifiers."""
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise OperationError(response.status_code, "response_too_large")
    if not response.content and 200 <= response.status_code < 300:
        return None
    try:
        data = response.json()
    except ValueError:
        if 200 <= response.status_code < 300:
            raise OperationError(response.status_code, "invalid_json") from None
        data = {}
    code = (
        data.get(
            "err_code",
            data.get("errcode", data.get("code")),
        )
        if isinstance(data, dict)
        else None
    )
    if not 200 <= response.status_code < 300 or code not in (None, 0, "0"):
        raise operation_error(
            response.status_code,
            code,
            response.headers.get("x-acs-request-id")
            or response.headers.get("x-tps-trace-id"),
        )
    return data


def operation_error(
    status: int, code: object, request_id: object = None
) -> OperationError:
    """Normalize safe vendor identifiers after transport-specific decoding."""
    safe_code = str(code or "unknown")
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", safe_code) is None:
        safe_code = "unknown"
    if (
        not isinstance(request_id, str)
        or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id) is None
    ):
        request_id = None
    error_type = OperationError
    if status in (401, 403):
        error_type = OperationPermissionDenied
    elif status == 429:
        error_type = OperationRateLimited
    return error_type(status, safe_code, request_id)
