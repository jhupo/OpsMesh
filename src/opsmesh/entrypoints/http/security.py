"""Bearer authentication and explicit resource authorization at the HTTP boundary."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import UserPrincipal

if TYPE_CHECKING:
    from opsmesh.bootstrap.application import OpsMeshApplication

bearer = HTTPBearer(auto_error=False)


def get_application(request: Request) -> OpsMeshApplication:
    return cast("OpsMeshApplication", request.app.state.opsmesh)


async def require_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> UserPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise OpsMeshError("UNAUTHENTICATED", "A valid Bearer session is required")
    application = get_application(request)
    principal = await application.services.accounts.authenticate(credentials.credentials)
    policy = await application.services.configuration.admission_policy()
    await application.services.rate_limiter.check(
        f"http:user:{principal.id}",
        limit=policy.requests_per_minute,
    )
    request.state.principal_id = principal.id
    request.state.bearer_token = credentials.credentials
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        # Fail before mutation if an audit intent cannot be durably recorded.
        await application.services.audit.record(
            f"http.{request.method.lower()}.requested",
            request.url.path,
            actor_id=principal.id,
            outcome="requested",
            event_id=f"{request.state.request_id}:intent",
        )
    return principal


CurrentUser = Annotated[UserPrincipal, Depends(require_user)]


async def authorize(
    request: Request,
    principal: UserPrincipal,
    action: str,
    resource: str,
    *,
    owner_id: str | None = None,
) -> None:
    await get_application(request).services.accounts.authorize(
        principal,
        action,
        resource,
        owner_id=owner_id,
    )


async def can_access(
    request: Request,
    principal: UserPrincipal,
    action: str,
    resource: str,
    *,
    owner_id: str | None = None,
) -> bool:
    try:
        await authorize(request, principal, action, resource, owner_id=owner_id)
        return True
    except OpsMeshError as exc:
        if exc.code in {"FORBIDDEN", "AUTHORIZATION_DENIED"}:
            return False
        raise


def validate_secret_reference(value: str) -> None:
    if not re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]*", value):
        raise OpsMeshError("CONFIG_INVALID", "Credentials must use deployed environment references")


def reject_secret_values(value: Any) -> None:
    """For configuration objects only; never apply to tool argument schemas or user text."""
    prohibited = {
        "api_key",
        "x_api_key",
        "password",
        "access_token",
        "refresh_token",
        "authorization",
        "proxy_authorization",
        "client_secret",
        "secret",
        "token",
    }
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold().replace("-", "_") in prohibited:
                raise OpsMeshError(
                    "CONFIG_INVALID", "Secret values cannot be stored in HTTP configuration"
                )
            reject_secret_values(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            reject_secret_values(item)
