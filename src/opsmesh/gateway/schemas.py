from datetime import datetime
from typing import Any

from pydantic import Field

from opsmesh.shared.schemas import RecordView, RequestSchema, ResourceView


class CreateChannelRequest(RequestSchema):
    name: str = Field(min_length=1, max_length=100)
    adapter: str = Field(min_length=1, max_length=100)
    plugin_id: str | None = None
    agent_id: str
    config: dict[str, Any]


class UpdateChannelRequest(RequestSchema):
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class BindChannelIdentityRequest(RequestSchema):
    user_id: str


class ChannelView(RecordView):
    owner_id: str
    name: str
    adapter: str
    plugin_id: str | None
    agent_id: str
    enabled: bool
    revision: int
    worker_id: str | None
    lease_until: datetime | None
    capabilities: dict[str, Any]


class ChannelIdentityView(ResourceView):
    channel_id: str
    external_user_id: str
    user_id: str


class DeliveryView(RecordView):
    channel_id: str
    task_id: str
    status: str
    attempts: int
    error_code: str | None


class DeliveryDetail(DeliveryView):
    receipt: Any = None


class CreateOperationRequest(RequestSchema):
    operation: str = Field(min_length=1, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=100)


class OperationView(RecordView):
    channel_id: str
    owner_id: str
    operation: str
    idempotency_key: str
    status: str
    attempts: int
    error_code: str | None
    finished_at: datetime | None


class OperationDetail(OperationView):
    result: Any = None


class ChannelEventView(RecordView):
    channel_id: str
    external_event_id: str
    event_type: str
    user_id: str | None
    task_id: str | None
    status: str
    error_code: str | None


class ChannelEventDetail(ChannelEventView):
    payload: dict[str, Any]
