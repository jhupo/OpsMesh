"""Stable domain input contracts for workspace tenant operations.

The API layer owns validation and serialization.  Tenant services consume these
small structural contracts so the domain does not import FastAPI/Pydantic
transport models.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


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
