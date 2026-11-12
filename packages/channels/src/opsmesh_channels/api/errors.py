"""API channel error types with client-safe messages."""

from __future__ import annotations

from collections.abc import Mapping


class APIError(Exception):
    """Base class for API channel failures."""


class APIConfigurationError(APIError, ValueError):
    """The API channel cannot start with the supplied configuration."""


class RouteRegistrationError(APIError, ValueError):
    """A plugin route declaration is invalid or cannot be installed."""


class RouteConflict(RouteRegistrationError):
    """A route declaration conflicts with an existing registration."""


class HTTPError(APIError):
    """A request failure that maps directly to an HTTP response.

    The message is sent to the client, so callers must not include internal
    exception text.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Create a safe request error with optional response headers."""
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.headers = dict(headers or {})
