"""Notification subscriptions use bounded, explicit event and template fields."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from opsmesh.framework.adapters import ReplyTarget
from opsmesh.framework.typing import OpsMeshContract


class NotificationSubscription(OpsMeshContract):
    id: str = Field(min_length=1, max_length=100)
    owner_id: str = Field(min_length=1)
    event_types: tuple[str, ...] = Field(min_length=1, max_length=32)
    target: ReplyTarget | None = None
    template: str = Field(default="${event_type}: ${resource_id} (${status})", max_length=4000)
    enabled: bool = True
    revision: int = Field(default=1, ge=1)

    @field_validator("template")
    @classmethod
    def validate_template(cls, value: str) -> str:
        from string import Template

        template = Template(value)
        if not template.is_valid() or set(template.get_identifiers()) - {
            "event_type",
            "resource_id",
            "status",
            "summary",
        }:
            raise ValueError("Template supports event_type, resource_id, status and summary only")
        return value

    @field_validator("event_types")
    @classmethod
    def validate_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or len(item) > 128 or "*" in item for item in value):
            raise ValueError("Subscriptions require exact event types")
        return tuple(dict.fromkeys(value))


class NotificationRecord(OpsMeshContract):
    id: str
    owner_id: str
    event_id: str
    event_type: str
    resource_id: str
    content: str
    target: ReplyTarget | None = None
    status: Literal["pending", "delivering", "sent", "failed", "unknown"] = "pending"
    read: bool = False
    attempts: int = 0
    created_at: datetime
    delivered_at: datetime | None = None
    lease_until: datetime | None = None
    delivery_id: str | None = None
    error_code: str | None = None
    revision: int = 1
