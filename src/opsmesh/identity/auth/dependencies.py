"""Resolve the CurrentUser from a login session or a verified delegation."""

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from opsmesh.identity.auth.contracts import DelegationSubjectResolver
from opsmesh.identity.auth.service import AuthService
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.errors import AuthenticationError
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.http.dependencies import get_db_session

SESSION_HEADER = "X-Session-ID"
INSTALLATION_HEADER = "X-OpsMesh-Installation-Id"
INSTANCE_HEADER = "X-OpsMesh-Instance-Id"
INVOCATION_HEADER = "X-OpsMesh-Invocation-Id"
NAMESPACE_HEADER = "X-OpsMesh-Namespace"

DbSession = Annotated[Session, Depends(get_db_session)]


def get_auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth_service", None)
    if not isinstance(service, AuthService):
        raise AuthenticationError("Authentication is unavailable")
    return service


def get_delegation_resolver(request: Request) -> DelegationSubjectResolver:
    resolver = getattr(request.app.state, "delegation_resolver", None)
    if resolver is None:
        raise AuthenticationError("Internal delegation is not available")
    return cast(DelegationSubjectResolver, resolver)


def current_user(
    session: DbSession,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    resolver: Annotated[DelegationSubjectResolver, Depends(get_delegation_resolver)],
    x_session_id: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
    x_installation_id: Annotated[str | None, Header(alias=INSTALLATION_HEADER)] = None,
    x_instance_id: Annotated[str | None, Header(alias=INSTANCE_HEADER)] = None,
    x_invocation_id: Annotated[str | None, Header(alias=INVOCATION_HEADER)] = None,
    x_namespace: Annotated[str | None, Header(alias=NAMESPACE_HEADER)] = None,
) -> CurrentUser:
    """Authenticate one request from its verified identity channel.

    A user session is the external channel; the delegation headers are only
    honored when the injected resolver verifies them. Neither channel trusts
    identifiers from the request body.
    """
    if x_session_id is not None:
        with session_transaction(session):
            return auth_service.authenticate(session, token=_session_token(x_session_id))
    if x_installation_id is None or x_instance_id is None or x_invocation_id is None:
        raise AuthenticationError("A session or a verified delegation is required")
    if x_namespace is None:
        raise AuthenticationError("A delegated call requires its package namespace")
    with session_transaction(session):
        user = resolver.resolve_delegation(
            session,
            installation_id=_uuid(x_installation_id, INSTALLATION_HEADER),
            instance_id=_uuid(x_instance_id, INSTANCE_HEADER),
            invocation_id=_uuid(x_invocation_id, INVOCATION_HEADER),
            namespace=x_namespace,
        )
    if user is None:
        raise AuthenticationError("The delegated invocation is not authorized")
    return user


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]


def delegated_user(
    session: DbSession,
    resolver: Annotated[DelegationSubjectResolver, Depends(get_delegation_resolver)],
    x_installation_id: Annotated[str | None, Header(alias=INSTALLATION_HEADER)] = None,
    x_instance_id: Annotated[str | None, Header(alias=INSTANCE_HEADER)] = None,
    x_invocation_id: Annotated[str | None, Header(alias=INVOCATION_HEADER)] = None,
    x_namespace: Annotated[str | None, Header(alias=NAMESPACE_HEADER)] = None,
) -> CurrentUser:
    """Resolve a request that may only arrive through a verified delegation.

    The session channel is not consulted here: an operation reserved for
    internal invocations can never be reached with a user session, even one
    presented alongside delegation headers.
    """
    if x_installation_id is None or x_instance_id is None or x_invocation_id is None:
        raise AuthenticationError("A verified delegation is required")
    if x_namespace is None:
        raise AuthenticationError("A delegated call requires its package namespace")
    with session_transaction(session):
        user = resolver.resolve_delegation(
            session,
            installation_id=_uuid(x_installation_id, INSTALLATION_HEADER),
            instance_id=_uuid(x_instance_id, INSTANCE_HEADER),
            invocation_id=_uuid(x_invocation_id, INVOCATION_HEADER),
            namespace=x_namespace,
        )
    if user is None:
        raise AuthenticationError("The delegated invocation is not authorized")
    return user


DelegatedUserDep = Annotated[CurrentUser, Depends(delegated_user)]


def _session_token(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise AuthenticationError("The session token is malformed") from exc


def _uuid(value: str, header: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise AuthenticationError(f"The {header} header is malformed") from exc
