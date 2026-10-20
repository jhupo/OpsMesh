"""Typed SDK errors with a stable machine-readable code.

Every error carries ``code`` (stable for program branching), a safe ``message``
(no secrets), the server ``request_id`` for support correlation, and an
optional ``retry_after``. ``details`` never contains credentials, full prompts
or raw payloads; sensitive values are summarized by the server boundary.
"""

from __future__ import annotations

from typing import ClassVar, Final


class OpsMeshError(Exception):
    """Base class for every SDK-visible failure."""

    code: ClassVar[str] = "opsmesh_error"

    def __init__(
        self,
        message: str,
        *,
        request_id: str | None = None,
        retry_after: float | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.request_id = request_id
        self.retry_after = retry_after
        self.details: dict[str, object] = details or {}

    def __str__(self) -> str:
        if self.request_id:
            return f"[{self.code}] {self.message} (request_id={self.request_id})"
        return f"[{self.code}] {self.message}"


class AuthenticationError(OpsMeshError):
    """The session is missing, expired or revoked."""

    code: ClassVar[str] = "unauthenticated"


class AuthorizationError(OpsMeshError):
    """The current subject may not perform this operation in this scope."""

    code: ClassVar[str] = "permission_denied"


class NotFoundError(OpsMeshError):
    """The referenced object does not exist or is not visible to the subject."""

    code: ClassVar[str] = "not_found"


class ValidationError(OpsMeshError):
    """The request violates the schema or a declared contract."""

    code: ClassVar[str] = "validation_failed"


class ConflictError(OpsMeshError):
    """An idempotency key was reused with a different input, or state forbids it."""

    code: ClassVar[str] = "conflict"


class RateLimitError(OpsMeshError):
    """The gateway or a resource quota rejected the call; retry after the hint."""

    code: ClassVar[str] = "rate_limited"


class UnknownEffectError(OpsMeshError):
    """An external effect could not be confirmed; reconcile before retrying."""

    code: ClassVar[str] = "needs_attention"


class TransportError(OpsMeshError):
    """The call never reached a decidable server state (connection, 5xx, protocol)."""

    code: ClassVar[str] = "unavailable"


class TimeoutError(OpsMeshError):
    """The deadline expired before a decidable answer arrived."""

    code: ClassVar[str] = "timeout"


class CancelledError(OpsMeshError):
    """The caller cancelled the operation; no external effect is implied."""

    code: ClassVar[str] = "cancelled"


class SecurityError(OpsMeshError):
    """A delegation boundary was violated (forged context, bad signature)."""

    code: ClassVar[str] = "security_violation"


# Several stable server codes describe the same caller-facing failure; the
# alias table keeps one typed error per failure class while every code the
# platform envelope can carry stays branchable.
_CODE_ALIASES: Final[dict[str, type[OpsMeshError]]] = {
    "invalid_credentials": AuthenticationError,
    "unauthorized": AuthenticationError,
    "user_disabled": AuthorizationError,
    "forbidden": AuthorizationError,
    "policy_denied": AuthorizationError,
    "invalid_argument": ValidationError,
    "invalid_avatar": ValidationError,
    "validation_error": ValidationError,
    "bad_request": ValidationError,
    "payload_too_large": ValidationError,
    "method_not_allowed": ValidationError,
    "duplicate_user": ConflictError,
    "invitation_state": ConflictError,
    "quota_reservation": ConflictError,
    "idempotency_key_reused": ConflictError,
    "idempotency_resource_missing": ConflictError,
    "quota_exceeded": RateLimitError,
    "capacity_exceeded": RateLimitError,
    "idempotency_in_progress": RateLimitError,
    "service_unavailable": TransportError,
    "session_blocked": ConflictError,
    "session_not_closed": ConflictError,
    "session_not_open": ConflictError,
    "session_has_active_runs": ConflictError,
    "history_operation_in_flight": ConflictError,
    "history_operation_conflict": ConflictError,
    "history_operation_uncertain": UnknownEffectError,
    "channel_epoch": ConflictError,
    "owner_unavailable": ConflictError,
}

_ERROR_BY_CODE: Final[dict[str, type[OpsMeshError]]] = {
    error.code: error
    for error in (
        AuthenticationError,
        AuthorizationError,
        NotFoundError,
        ValidationError,
        ConflictError,
        RateLimitError,
        UnknownEffectError,
        TransportError,
        TimeoutError,
        CancelledError,
        SecurityError,
    )
} | _CODE_ALIASES


def error_for_code(code: str) -> type[OpsMeshError]:
    """Map a server error code onto its typed SDK error."""
    return _ERROR_BY_CODE.get(code, OpsMeshError)
