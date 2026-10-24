from collections.abc import Mapping
from typing import Any


class DomainError(Exception):
    """An actionable failure with a stable code and explicitly public details."""

    code = "domain_error"
    status_code = 400
    default_message = "Request failed"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.message = message if message is not None else self.default_message
        self.code = code if code is not None else type(self).code
        self.details = dict(details or {})
        super().__init__(self.message)


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"
    default_message = "Resource not found"


class ForbiddenError(DomainError):
    status_code = 403
    code = "forbidden"
    default_message = "Forbidden"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"
    default_message = "Conflict"


class InvalidArgumentError(DomainError):
    code = "invalid_argument"
    default_message = "Invalid argument"


class QuotaExceededError(DomainError):
    status_code = 429
    code = "quota_exceeded"
    default_message = "Quota exceeded"
