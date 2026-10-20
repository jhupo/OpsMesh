"""Identity errors: authentication, session and user state failures."""

from __future__ import annotations

from opsmesh.shared.errors import DomainError


class AuthenticationError(DomainError):
    """The session is missing, invalid, expired or revoked."""

    def __init__(
        self,
        message: str = "Authentication required",
        *,
        code: str = "unauthenticated",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, status_code=401, details=details or {})


class InvalidCredentialsError(DomainError):
    """The presented credentials do not match any active account."""

    def __init__(
        self,
        message: str = "Invalid credentials",
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            message, code="invalid_credentials", status_code=401, details=details or {}
        )


class UserDisabledError(DomainError):
    """The account exists but may not authenticate or act."""

    def __init__(
        self,
        message: str = "The user account is disabled",
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code="user_disabled", status_code=403, details=details or {})


class DuplicateUserError(DomainError):
    """The email already belongs to another account."""

    def __init__(
        self,
        message: str = "The email is already registered",
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code="duplicate_user", status_code=409, details=details or {})


class InvitationStateError(DomainError):
    """The invitation is not in a state that allows the requested transition."""

    def __init__(
        self,
        message: str = "The invitation is not pending",
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code="invitation_state", status_code=409, details=details or {})


class QuotaExceededError(DomainError):
    """The user's token or storage ledger cannot admit the reservation."""

    def __init__(
        self,
        message: str = "The user quota cannot admit this reservation",
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code="quota_exceeded", status_code=429, details=details or {})


class QuotaReservationError(DomainError):
    """The reservation is unknown or already settled with a different amount."""

    def __init__(
        self,
        message: str = "The quota reservation is not reservable",
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code="quota_reservation", status_code=409, details=details or {})


class InvalidAvatarError(DomainError):
    """The uploaded avatar is not an allowed, decodable, bounded image."""

    def __init__(
        self,
        message: str = "The avatar image is not acceptable",
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code="invalid_avatar", status_code=400, details=details or {})
