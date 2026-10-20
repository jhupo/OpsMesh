"""HTTP contracts for channel connections, routing and durable receipts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChannelAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configuration_id: UUID | None = None
    channel_type: str = Field(min_length=1, max_length=50)
    external_account_id: str = Field(min_length=1, max_length=200)
    installation_id: UUID
    version_id: UUID
    configuration: dict[str, object] = Field(default_factory=dict)


class ChannelAccountView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    owner_id: UUID
    configuration_id: UUID | None
    channel_type: str
    external_account_id: str
    installation_id: UUID
    version_id: UUID
    status: str
    epoch: int
    configuration: dict[str, object]
    created_at: datetime
    updated_at: datetime


class ChannelIdentityMappingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    external_identity: str = Field(min_length=1, max_length=200)


class ChannelIdentityMappingView(ChannelIdentityMappingCreate):
    id: UUID
    account_id: UUID
    status: str


class ChannelMappingStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "revoked"]


class ChannelRouteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_key: str = Field(min_length=1, max_length=400)
    configuration_id: UUID
    flow_revision_id: UUID
    enabled: bool = True
    input_mapping: dict[str, str] = Field(default_factory=dict)


class ChannelRouteView(ChannelRouteCreate):
    id: UUID
    account_id: UUID


class ChannelInboxView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    account_id: UUID
    connection_epoch: int
    external_message_id: str
    conversation_key: str
    payload_digest: str
    status: str
    flow_run_id: UUID | None
    failure_code: str | None
    received_at: datetime


class ChannelDeliveryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    account_id: UUID
    connection_epoch: int
    idempotency_key: str
    conversation_key: str
    payload_digest: str
    status: str
    attempts: int
    external_message_id: str | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime


class ChannelAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_id: UUID
    version: int = Field(ge=1)


class ChannelReceiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    epoch: int = Field(ge=1)
    external_message_id: str = Field(min_length=1, max_length=240)
    conversation_key: str = Field(min_length=1, max_length=400)
    external_identity: str = Field(min_length=1, max_length=200)
    payload: dict[str, object]
    attachments: list[ChannelAttachment] = Field(default_factory=list, max_length=10)


class ChannelReceiveView(BaseModel):
    inbox_id: UUID
    accepted: bool
    status: str


class ChannelDeliveryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=240)
    conversation_key: str = Field(min_length=1, max_length=400)
    payload: dict[str, object]
    attachments: list[ChannelAttachment] = Field(default_factory=list, max_length=10)


class ChannelDeliveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(pattern="^(sent|failed|needs_attention)$")
    external_message_id: str | None = Field(default=None, max_length=240)
    failure_code: str | None = Field(default=None, max_length=100)


class ChannelConnectionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_epoch: int = Field(ge=1)
    enabled: bool


class ChannelConnectionAccess(BaseModel):
    account_id: UUID
    epoch: int
    configuration: dict[str, object]


class ChannelDeliveryClaim(ChannelDeliveryView):
    payload: dict[str, object]


class ChannelReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["needs_attention", "sent", "failed"]
    evidence: str = Field(min_length=1, max_length=2000)
    external_message_id: str | None = Field(default=None, min_length=1, max_length=240)
