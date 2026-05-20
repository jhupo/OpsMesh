from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DomainError(Exception):
    message: str
    code: str = "domain_error"
    status_code: int = 400
    details: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)


class NotFoundError(DomainError):
    def __init__(
        self,
        message: str = "Resource not found",
        *,
        code: str = "not_found",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, status_code=404, details=details or {})


class ForbiddenError(DomainError):
    def __init__(
        self,
        message: str = "Forbidden",
        *,
        code: str = "forbidden",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, status_code=403, details=details or {})


class ConflictError(DomainError):
    def __init__(
        self,
        message: str = "Conflict",
        *,
        code: str = "conflict",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, status_code=409, details=details or {})


class QuotaExceededError(DomainError):
    def __init__(
        self,
        message: str = "Quota exceeded",
        *,
        code: str = "quota_exceeded",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, status_code=409, details=details or {})


class PolicyDeniedError(DomainError):
    def __init__(
        self,
        message: str = "Action denied by policy",
        *,
        code: str = "policy_denied",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, status_code=403, details=details or {})
