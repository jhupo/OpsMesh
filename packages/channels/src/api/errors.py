"""API channel error types with client-safe messages."""

from __future__ import annotations

from collections.abc import Mapping


class APIError(Exception):
    """Base class for API channel failures."""


class APIConfigurationError(APIError, ValueError):
    """The API channel cannot start with the supplied configuration."""


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


def request_error(error: BaseException) -> HTTPError:
    """Classify JSON and SSE failures without exposing internal details."""
    import logging

    import anyio
    from pydantic import ValidationError
    from starlette.exceptions import HTTPException
    from starlette.requests import ClientDisconnect

    from opsmesh.plugins.invocation import PluginUnavailable
    from opsmesh.utils import flatten_exception_group

    from ..attachments import AttachmentError

    leaves = (
        list(flatten_exception_group(error))
        if isinstance(error, BaseExceptionGroup)
        else [error]
    )
    for leaf in leaves:
        if isinstance(
            leaf, (anyio.get_cancelled_exc_class(), ClientDisconnect)
        ):
            raise leaf
    if len(leaves) == 1:
        error = leaves[0]
    if isinstance(error, HTTPError):
        return error
    if isinstance(error, HTTPException):
        return HTTPError(
            error.status_code, str(error.detail), headers=error.headers
        )
    if isinstance(error, (AttachmentError, ValidationError)):
        return HTTPError(422, "Invalid message attachments")
    if isinstance(error, PluginUnavailable):
        return HTTPError(503, "connection is not ready")
    if isinstance(error, TimeoutError):
        return HTTPError(504, "request timed out")
    if isinstance(error, PermissionError):
        return HTTPError(403, "request is not permitted")
    logging.getLogger(__name__).error(
        "API request failed", exc_info=(type(error), error, error.__traceback__)
    )
    return HTTPError(500, "internal server error")
