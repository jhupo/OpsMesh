"""Injected contracts for account opening, identity lookup and delegation.

Authentication never imports the users module: the profile owner is reached
only through these contracts, which bootstrap assembles. The delegation
resolver exists so a verified internal invocation can be resolved into a
subject; no producer is registered before the extensions step, and an
unregistered resolver rejects every delegated call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.identity.context import CurrentUser
from opsmesh.identity.errors import AuthenticationError


@dataclass(frozen=True)
class OpenedAccount:
    """The result of opening an account profile through the injected owner."""

    user_id: UUID
    email: str
    display_name: str
    status: str
    platform_roles: frozenset[str]


class AccountOpening(Protocol):
    """Create the user profile, roles and defaults for a new account."""

    def open_account(
        self,
        session: Session,
        *,
        email: str,
        display_name: str,
    ) -> OpenedAccount: ...


class UserDirectory(Protocol):
    """Read the current identity value of one user."""

    def load_identity(self, session: Session, user_id: UUID) -> CurrentUser | None: ...

    def find_by_email(self, session: Session, email: str) -> CurrentUser | None: ...


class SecurityEventRecorder(Protocol):
    """Record a security fact; the governance owner is injected by bootstrap."""

    def record(
        self,
        session: Session,
        *,
        kind: str,
        severity: str,
        summary: str,
        details: dict[str, object],
        user_id: UUID | None,
    ) -> None: ...


class DelegationSubjectResolver(Protocol):
    """Resolve a verified internal delegation into a subject.

    The resolver re-validates the invocation and the permitted API surface;
    external headers alone never produce a subject.
    """

    def resolve_delegation(
        self,
        session: Session,
        *,
        installation_id: UUID,
        instance_id: UUID,
        invocation_id: UUID,
        namespace: str,
    ) -> CurrentUser | None: ...


class RejectingDelegationResolver:
    """The S4 resolver: no delegation producer is registered yet."""

    def resolve_delegation(
        self,
        session: Session,
        *,
        installation_id: UUID,
        instance_id: UUID,
        invocation_id: UUID,
        namespace: str,
    ) -> CurrentUser | None:
        raise AuthenticationError("Internal delegation is not available")
