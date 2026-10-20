"""Generated channels API group; regenerate instead of editing by hand.

Produced by scripts/generate_sdk_api.py from docs/openapi.json and
sdk/python/codegen.toml; the platform OpenAPI is the HTTP contract.
Stable operation ids of this group:

- ``channels.accounts.list``
- ``channels.accounts.create``
- ``channels.accounts.get``
- ``channels.accounts.connection.read``
- ``channels.accounts.connection.change``
- ``channels.accounts.deliveries.list``
- ``channels.accounts.deliveries.create``
- ``channels.accounts.inbox.list``
- ``channels.accounts.inbox.receive``
- ``channels.accounts.mappings.list``
- ``channels.accounts.mappings.create``
- ``channels.accounts.mappings.update``
- ``channels.accounts.routes.list``
- ``channels.accounts.routes.create``
- ``channels.accounts.routes.delete``
- ``channels.deliveries.claim``
- ``channels.deliveries.reconcile``
- ``channels.deliveries.result``
"""

from __future__ import annotations

import builtins
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from opsmesh_sdk.transport.contracts import (
    GroupChannel,
    TransportRequest,
    decode_array,
)


class ChannelAccountCreate(BaseModel):
    channel_type: str
    configuration: dict[str, object] | None = None
    configuration_id: UUID | None = None
    external_account_id: str
    installation_id: UUID
    version_id: UUID


class ChannelAccountView(BaseModel):
    channel_type: str
    configuration: dict[str, object]
    configuration_id: UUID | None
    created_at: datetime
    epoch: int
    external_account_id: str
    id: UUID
    installation_id: UUID
    owner_id: UUID
    status: str
    updated_at: datetime
    version_id: UUID


class ChannelAttachment(BaseModel):
    file_id: UUID
    version: int


class ChannelConnectionAccess(BaseModel):
    account_id: UUID
    configuration: dict[str, object]
    epoch: int


class ChannelConnectionChange(BaseModel):
    enabled: bool
    expected_epoch: int


class ChannelDeliveryClaim(BaseModel):
    account_id: UUID
    attempts: int
    connection_epoch: int
    conversation_key: str
    created_at: datetime
    external_message_id: str | None
    failure_code: str | None
    id: UUID
    idempotency_key: str
    payload: dict[str, object]
    payload_digest: str
    status: str
    updated_at: datetime


class ChannelDeliveryResult(BaseModel):
    external_message_id: str | None = None
    failure_code: str | None = None
    status: str


class ChannelDeliveryView(BaseModel):
    account_id: UUID
    attempts: int
    connection_epoch: int
    conversation_key: str
    created_at: datetime
    external_message_id: str | None
    failure_code: str | None
    id: UUID
    idempotency_key: str
    payload_digest: str
    status: str
    updated_at: datetime


class ChannelIdentityMappingCreate(BaseModel):
    external_identity: str
    user_id: UUID


class ChannelIdentityMappingView(BaseModel):
    account_id: UUID
    external_identity: str
    id: UUID
    status: str
    user_id: UUID


class ChannelInboxView(BaseModel):
    account_id: UUID
    connection_epoch: int
    conversation_key: str
    external_message_id: str
    failure_code: str | None
    flow_run_id: UUID | None
    id: UUID
    payload_digest: str
    received_at: datetime
    status: str


class ChannelMappingStatus(BaseModel):
    status: str


class ChannelReceiveView(BaseModel):
    accepted: bool
    inbox_id: UUID
    status: str


class ChannelReconciliation(BaseModel):
    evidence: str
    external_message_id: str | None = None
    status: str


class ChannelRouteCreate(BaseModel):
    configuration_id: UUID
    conversation_key: str
    enabled: bool = True
    flow_revision_id: UUID
    input_mapping: dict[str, str] | None = None


class ChannelRouteView(BaseModel):
    account_id: UUID
    configuration_id: UUID
    conversation_key: str
    enabled: bool = True
    flow_revision_id: UUID
    id: UUID
    input_mapping: dict[str, str] | None = None


class ChannelDeliveryCreate(BaseModel):
    attachments: list[ChannelAttachment] | None = None
    conversation_key: str
    idempotency_key: str
    payload: dict[str, object]


class ChannelReceiveRequest(BaseModel):
    attachments: list[ChannelAttachment] | None = None
    conversation_key: str
    epoch: int
    external_identity: str
    external_message_id: str
    payload: dict[str, object]


class ChannelsAccountsConnectionClient:
    """Generated client for the ``channels.accounts.connection`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def read(self, account_id: UUID, *, expected_epoch: int) -> ChannelConnectionAccess:
        query = {
            name: str(value)
            for name, value in (
                ("expected_epoch", expected_epoch),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/channels/accounts/{account_id}/connection",
                query=query,
            ),
            self._channel.context(),
        )
        return ChannelConnectionAccess.model_validate(response.body)

    async def change(
        self,
        account_id: UUID,
        request: ChannelConnectionChange,
    ) -> ChannelAccountView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/channels/accounts/{account_id}/connection",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return ChannelAccountView.model_validate(response.body)


class ChannelsAccountsDeliveriesClient:
    """Generated client for the ``channels.accounts.deliveries`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        account_id: UUID,
        *,
        after: UUID | None = None,
        limit: int = 100,
    ) -> builtins.list[ChannelDeliveryView]:
        query = {
            name: str(value)
            for name, value in (
                ("after", after),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/channels/accounts/{account_id}/deliveries",
                query=query,
            ),
            self._channel.context(),
        )
        return [ChannelDeliveryView.model_validate(item) for item in decode_array(response)]

    async def create(self, account_id: UUID, request: ChannelDeliveryCreate) -> ChannelDeliveryView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/channels/accounts/{account_id}/deliveries",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ChannelDeliveryView.model_validate(response.body)


class ChannelsAccountsInboxClient:
    """Generated client for the ``channels.accounts.inbox`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        account_id: UUID,
        *,
        after: UUID | None = None,
        limit: int = 100,
    ) -> builtins.list[ChannelInboxView]:
        query = {
            name: str(value)
            for name, value in (
                ("after", after),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/channels/accounts/{account_id}/inbox",
                query=query,
            ),
            self._channel.context(),
        )
        return [ChannelInboxView.model_validate(item) for item in decode_array(response)]

    async def receive(self, account_id: UUID, request: ChannelReceiveRequest) -> ChannelReceiveView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/channels/accounts/{account_id}/inbox",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ChannelReceiveView.model_validate(response.body)


class ChannelsAccountsMappingsClient:
    """Generated client for the ``channels.accounts.mappings`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        account_id: UUID,
        *,
        after: UUID | None = None,
        limit: int = 100,
    ) -> builtins.list[ChannelIdentityMappingView]:
        query = {
            name: str(value)
            for name, value in (
                ("after", after),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/channels/accounts/{account_id}/mappings",
                query=query,
            ),
            self._channel.context(),
        )
        return [ChannelIdentityMappingView.model_validate(item) for item in decode_array(response)]

    async def create(
        self,
        account_id: UUID,
        request: ChannelIdentityMappingCreate,
    ) -> ChannelIdentityMappingView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/channels/accounts/{account_id}/mappings",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return ChannelIdentityMappingView.model_validate(response.body)

    async def update(
        self,
        account_id: UUID,
        mapping_id: UUID,
        request: ChannelMappingStatus,
    ) -> ChannelIdentityMappingView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/channels/accounts/{account_id}/mappings/{mapping_id}",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return ChannelIdentityMappingView.model_validate(response.body)


class ChannelsAccountsRoutesClient:
    """Generated client for the ``channels.accounts.routes`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        account_id: UUID,
        *,
        after: UUID | None = None,
        limit: int = 100,
    ) -> builtins.list[ChannelRouteView]:
        query = {
            name: str(value)
            for name, value in (
                ("after", after),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/channels/accounts/{account_id}/routes",
                query=query,
            ),
            self._channel.context(),
        )
        return [ChannelRouteView.model_validate(item) for item in decode_array(response)]

    async def create(self, account_id: UUID, request: ChannelRouteCreate) -> ChannelRouteView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/channels/accounts/{account_id}/routes",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ChannelRouteView.model_validate(response.body)

    async def delete(self, account_id: UUID, route_id: UUID) -> None:
        await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/channels/accounts/{account_id}/routes/{route_id}",
            ),
            self._channel.context(),
        )


class ChannelsAccountsClient:
    """Generated client for the ``channels.accounts`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.connection = ChannelsAccountsConnectionClient(channel)
        self.deliveries = ChannelsAccountsDeliveriesClient(channel)
        self.inbox = ChannelsAccountsInboxClient(channel)
        self.mappings = ChannelsAccountsMappingsClient(channel)
        self.routes = ChannelsAccountsRoutesClient(channel)

    async def list(
        self,
        *,
        after: UUID | None = None,
        limit: int = 100,
        configuration_id: UUID | None = None,
    ) -> builtins.list[ChannelAccountView]:
        """List personal accounts by default, or accounts in one authorized exact scope."""
        query = {
            name: str(value)
            for name, value in (
                ("after", after),
                ("limit", limit),
                ("configuration_id", configuration_id),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/channels/accounts",
                query=query,
            ),
            self._channel.context(),
        )
        return [ChannelAccountView.model_validate(item) for item in decode_array(response)]

    async def create(self, request: ChannelAccountCreate) -> ChannelAccountView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/channels/accounts",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ChannelAccountView.model_validate(response.body)

    async def get(self, account_id: UUID) -> ChannelAccountView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/channels/accounts/{account_id}",
            ),
            self._channel.context(),
        )
        return ChannelAccountView.model_validate(response.body)


class ChannelsDeliveriesClient:
    """Generated client for the ``channels.deliveries`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def claim(self, delivery_id: UUID) -> ChannelDeliveryClaim:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/channels/deliveries/{delivery_id}/claim",
            ),
            self._channel.context(),
        )
        return ChannelDeliveryClaim.model_validate(response.body)

    async def reconcile(
        self,
        delivery_id: UUID,
        request: ChannelReconciliation,
    ) -> ChannelDeliveryView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/channels/deliveries/{delivery_id}/reconcile",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ChannelDeliveryView.model_validate(response.body)

    async def result(
        self,
        delivery_id: UUID,
        request: ChannelDeliveryResult,
    ) -> ChannelDeliveryView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/channels/deliveries/{delivery_id}/result",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ChannelDeliveryView.model_validate(response.body)


class ChannelsClient:
    """The channels API group."""

    def __init__(self, channel: GroupChannel) -> None:
        self.accounts = ChannelsAccountsClient(channel)
        self.deliveries = ChannelsDeliveriesClient(channel)


def create_channels_client(channel: GroupChannel) -> ChannelsClient:
    """Create the channels group client over one composed platform channel."""
    return ChannelsClient(channel)


__all__ = [
    "ChannelAccountCreate",
    "ChannelAccountView",
    "ChannelAttachment",
    "ChannelConnectionAccess",
    "ChannelConnectionChange",
    "ChannelDeliveryClaim",
    "ChannelDeliveryCreate",
    "ChannelDeliveryResult",
    "ChannelDeliveryView",
    "ChannelIdentityMappingCreate",
    "ChannelIdentityMappingView",
    "ChannelInboxView",
    "ChannelMappingStatus",
    "ChannelReceiveRequest",
    "ChannelReceiveView",
    "ChannelReconciliation",
    "ChannelRouteCreate",
    "ChannelRouteView",
    "ChannelsAccountsClient",
    "ChannelsAccountsConnectionClient",
    "ChannelsAccountsDeliveriesClient",
    "ChannelsAccountsInboxClient",
    "ChannelsAccountsMappingsClient",
    "ChannelsAccountsRoutesClient",
    "ChannelsClient",
    "ChannelsDeliveriesClient",
    "create_channels_client",
]
