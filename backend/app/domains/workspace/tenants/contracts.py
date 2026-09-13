"""Stable domain input contracts for workspace tenant operations.

The API layer owns validation and serialization.  Tenant services consume these
small structural contracts so the domain does not import FastAPI/Pydantic
transport models.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID


class WorkspaceCreatePayload(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def slug(self) -> str: ...

    @property
    def settings(self) -> Mapping[str, object]: ...


class WorkspaceUpdatePayload(Protocol):
    @property
    def name(self) -> str | None: ...

    @property
    def status(self) -> str | None: ...

    @property
    def settings(self) -> Mapping[str, object] | None: ...


class WorkspaceMemberCreatePayload(Protocol):
    @property
    def user_id(self) -> UUID: ...

    @property
    def role(self) -> str: ...


class WorkspaceMemberUpdatePayload(Protocol):
    @property
    def role(self) -> str | None: ...

    @property
    def status(self) -> str | None: ...


class WorkspaceInviteCreatePayload(Protocol):
    @property
    def email(self) -> str: ...

    @property
    def role(self) -> str: ...

    @property
    def expires_at(self) -> datetime: ...

    @property
    def invitee_user_id(self) -> UUID | None: ...


class WorkspaceInviteAcceptPayload(Protocol):
    @property
    def token(self) -> str: ...


class WorkspaceQuotaUpsertItemPayload(Protocol):
    @property
    def quota_key(self) -> str: ...

    @property
    def limit_value(self) -> int: ...

    @property
    def unit(self) -> str: ...


class WorkspaceQuotaUpsertPayload(Protocol):
    @property
    def quotas(self) -> Sequence[WorkspaceQuotaUpsertItemPayload]: ...
