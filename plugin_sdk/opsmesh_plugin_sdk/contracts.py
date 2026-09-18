"""Data-only extension declarations; installation never authorizes execution."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class IncomingMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str = Field(min_length=1, max_length=160)
    conversation_id: str = Field(min_length=1, max_length=160)
    sender_id: str = Field(min_length=1, max_length=160)
    occurred_at: AwareDatetime
    text: str = Field(min_length=1, max_length=16_000)
    data: dict[str, object] = Field(default_factory=dict, max_length=64)


class CapabilityDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,119}$")
    kind: Literal["mcp_server", "skill", "message_trigger", "reply_channel"]
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    configuration_schema: dict[str, object] = Field(default_factory=dict)
    required_permissions: list[str] = Field(default_factory=list, max_length=32)


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[1] = 1
    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,119}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    name: str = Field(min_length=1, max_length=160)
    execution: Literal["remote"] = "remote"
    capabilities: list[CapabilityDeclaration] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_capabilities(self) -> PluginManifest:
        keys = [item.key for item in self.capabilities]
        if len(keys) != len(set(keys)):
            raise ValueError("Plugin capability keys must be unique")
        return self


class AcceptedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    automation_id: UUID
    external_event_id: str
    conversation_id: str
    status: str
    task_id: UUID | None
    reply_delivery_id: UUID | None
    error_code: str | None
