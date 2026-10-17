"""Workspace notification HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.workspaces.notifications.models import (
    MAX_BODY_LENGTH,
    MAX_TITLE_LENGTH,
    MAX_TYPE_LENGTH,
    NOTIFICATION_SEVERITIES,
)

SeverityName = Literal["info", "warning", "critical"]


class NotificationCreateRequest(BaseModel):
    """The producer contract; platform producers are the only callers today."""

    model_config = ConfigDict(extra="forbid")

    recipient_user_id: UUID
    notification_type: str = Field(min_length=1, max_length=MAX_TYPE_LENGTH)
    severity: SeverityName = "info"
    source_type: str = Field(min_length=1, max_length=MAX_TYPE_LENGTH)
    source_id: UUID | None = None
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    body: str = Field(default="", max_length=MAX_BODY_LENGTH)
    payload: dict[str, object] = Field(default_factory=dict)


class NotificationView(BaseModel):
    notification_id: UUID
    workspace_id: UUID
    recipient_user_id: UUID
    notification_type: str
    severity: str
    source_type: str
    source_id: UUID | None
    title: str
    body: str
    payload: dict[str, object]
    read_at: datetime | None
    archived_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    notifications: list[NotificationView]
    total: int
    unread_count: int


class MarkReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_ids: list[UUID] = Field(default_factory=list, max_length=200)
    mark_all: bool = False


class MarkReadView(BaseModel):
    marked_count: int


class NotificationPreferenceView(BaseModel):
    workspace_id: UUID
    user_id: UUID
    in_app_enabled: bool
    email_enabled: bool


class NotificationPreferenceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    in_app_enabled: bool | None = None
    email_enabled: bool | None = None


__all__ = [
    "NOTIFICATION_SEVERITIES",
    "MarkReadRequest",
    "MarkReadView",
    "NotificationCreateRequest",
    "NotificationPage",
    "NotificationPreferenceUpdateRequest",
    "NotificationPreferenceView",
    "NotificationView",
]
