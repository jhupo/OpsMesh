"""Stable domain input contracts for workspace tenant operations.

The API layer owns validation and serialization.  Tenant services consume these
small structural contracts so the domain does not import FastAPI/Pydantic
transport models.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


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
