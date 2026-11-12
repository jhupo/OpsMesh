"""Bearer token authentication for the generic HTTP channel."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass, field

from opsmesh.framework.drivers import Request

from ..context import UserContext
from .config import IdentitySettings, Settings
from .errors import APIConfigurationError, HTTPError

_AUTH_HEADER = "authorization"
_BEARER = "bearer"


def _unauthorized() -> HTTPError:
    return HTTPError(
        401,
        "authentication required",
        headers={"www-authenticate": "Bearer"},
    )


def _forbidden() -> HTTPError:
    return HTTPError(403, "insufficient permission")


@dataclass(frozen=True)
class Principal:
    """An authenticated caller and its permitted operation classes."""

    caller: UserContext
    allow_invoke: bool
    allow_admin: bool
    token: str = field(repr=False)

    def allows(self, access: str) -> bool:
        """Return whether this principal may use the requested access."""
        if access == "invoke":
            return self.allow_invoke
        if access == "admin":
            return self.allow_admin
        return False


def _resolve_token(reference: str) -> str:
    token = os.environ.get(reference)
    if token is None or not token.strip():
        raise APIConfigurationError(
            f"API token environment variable is missing: {reference}"
        )
    if not token.isascii() or any(not char.isprintable() for char in token):
        raise APIConfigurationError(
            f"API token environment variable must contain printable ASCII: "
            f"{reference}"
        )
    if any(char.isspace() for char in token):
        raise APIConfigurationError(
            f"API token environment variable contains whitespace: {reference}"
        )
    return token


class Authenticator:
    """Resolve trusted tokens to immutable caller identities."""

    def __init__(self, principals: tuple[Principal, ...]) -> None:
        """Create an authenticator over resolved principals."""
        if not principals:
            raise APIConfigurationError(
                "The API channel requires at least one identity"
            )
        self._principals = principals

    @classmethod
    def from_settings(cls, settings: Settings) -> Authenticator:
        """Resolve environment token references once during startup."""
        principals: list[Principal] = []
        seen: set[str] = set()
        for identity in settings.identities:
            token = _resolve_token(identity.token_ref)
            if token in seen:
                raise APIConfigurationError(
                    "API tokens must be distinct across identities"
                )
            seen.add(token)
            principals.append(_principal(identity, token))
        return cls(tuple(principals))

    def authenticate(self, request: Request) -> Principal:
        """Return the matching principal or raise a safe 401 error."""
        values = request.headers.getall(_AUTH_HEADER, [])
        if len(values) != 1:
            raise _unauthorized()
        scheme, separator, token = values[0].strip().partition(" ")
        if not separator or scheme.lower() != _BEARER or not token.strip():
            raise _unauthorized()
        token = token.strip()
        if not token.isascii() or not token.isprintable():
            raise _unauthorized()
        provided = token.encode("ascii")
        matched: Principal | None = None
        for principal in self._principals:
            if hmac.compare_digest(provided, principal.token.encode("ascii")):
                matched = principal
        if matched is None:
            raise _unauthorized()
        return matched

    @staticmethod
    def authorize(principal: Principal, access: str) -> None:
        """Raise a safe 403 error when the operation is not permitted."""
        if not principal.allows(access):
            raise _forbidden()


def _principal(identity: IdentitySettings, token: str) -> Principal:
    caller = UserContext(
        f"api:{identity.id}",
        name=identity.name or identity.id,
        groups=frozenset(identity.groups),
        roles=frozenset(identity.roles),
    )
    return Principal(
        caller=caller,
        allow_invoke=identity.allow_invoke,
        allow_admin=identity.allow_admin,
        token=token,
    )
