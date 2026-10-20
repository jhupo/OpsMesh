"""Worker-authenticated delegation; public headers are references, never proof."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.extensions.access import ScopeAuthorization, authorize_invocation
from opsmesh.extensions.invocation import load_invocation
from opsmesh.identity.auth.contracts import UserDirectory
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.errors import AuthenticationError


@dataclass(frozen=True)
class DelegatedCall:
    invocation_id: UUID
    operation: str
    method: str
    path: str


_CALL: ContextVar[DelegatedCall | None] = ContextVar("extension_delegated_call", default=None)


@contextmanager
def authenticated_call(call: DelegatedCall) -> Iterator[None]:
    token = _CALL.set(call)
    try:
        yield
    finally:
        _CALL.reset(token)


def current_call() -> DelegatedCall:
    call = _CALL.get()
    if call is None:
        raise AuthenticationError("Delegation requires the authenticated Worker channel")
    return call


class InvocationDelegation:
    def __init__(self, users: UserDirectory, scopes: ScopeAuthorization) -> None:
        self.users, self.scopes = users, scopes

    def resolve_delegation(
        self,
        session: Session,
        *,
        installation_id: UUID,
        instance_id: UUID,
        invocation_id: UUID,
        namespace: str,
    ) -> CurrentUser:
        call = current_call()
        if call.invocation_id != invocation_id:
            raise AuthenticationError("The delegation does not match the authenticated channel")
        invocation = load_invocation(session, invocation_id)
        user, _, installation = authorize_invocation(
            session,
            invocation,
            users=self.users,
            scopes=self.scopes,
            permission=call.operation,
        )
        if (installation.id, invocation.instance_id, installation.namespace) != (
            installation_id,
            instance_id,
            namespace,
        ):
            raise AuthenticationError("The delegation references do not match the Invocation")
        # Administration always requires an external current administrator
        # Session. An installation never delegates its installer's roles.
        return replace(user, platform_roles=frozenset())
