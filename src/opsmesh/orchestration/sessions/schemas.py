"""Model session and native history HTTP contracts.

The session views expose the stored facts only; the history contract
carries SDK history items as JSON objects, because the SDK owns their
shape and validates them when a runner consumes the session.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_HISTORY_APPEND_ITEMS = 100
DEFAULT_HISTORY_LIMIT = 100
MAX_HISTORY_LIMIT = 1000

SessionStatus = Literal["open", "closing", "closed", "blocked"]


class SessionView(BaseModel):
    """One model session as stored."""

    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    scope_id: UUID
    principal_id: UUID
    configuration_id: UUID | None
    owner_ref: str
    status: SessionStatus
    created_at: datetime
    updated_at: datetime


class SessionCreateRequest(BaseModel):
    """The scope a new session is opened in; the user scope is the default."""

    model_config = ConfigDict(extra="forbid")

    configuration_id: UUID | None = None


class HistoryAppendRequest(BaseModel):
    """The items one append stores, in order."""

    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_HISTORY_APPEND_ITEMS)


class HistoryAppendView(BaseModel):
    """How many items the append stored."""

    model_config = ConfigDict(extra="forbid")

    appended: int


class HistoryPage(BaseModel):
    """One bounded read of the native history, oldest first."""

    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]]


class HistoryItemView(BaseModel):
    """The item one pop removed, or nothing when the history was empty."""

    model_config = ConfigDict(extra="forbid")

    item: dict[str, Any] | None
