"""OpsMesh QQ adapter with independently owned transport lifetime."""

from typing import Any

from opsmesh_sdk.channels import (
    ChannelAdapter,
    ChannelCapabilities,
    ChannelMedia,
    DeliveryReceipt,
    EventSink,
    OutboundMessage,
)

from opsmesh.gateway.adapters.qq.client import QQClient
from opsmesh.gateway.adapters.qq.config import QQConfig
from opsmesh.gateway.adapters.qq.connection import QQConnection
from opsmesh.gateway.adapters.qq.events import parse_event
from opsmesh.gateway.adapters.qq.messages import send_message


class QQAdapter(ChannelAdapter):
    capabilities = ChannelCapabilities(
        inbound_media=True,
        outbound_media=True,
        streaming=True,
        typing=True,
        proactive=True,
        qr_binding=True,
        probe=True,
        recall=True,
    )

    def __init__(self, config: dict[str, Any], sink: EventSink, media: ChannelMedia) -> None:
        super().__init__(config, sink, media)
        self.client = QQClient(QQConfig.model_validate(config))
        self.connection = QQConnection(self.client)

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        return QQConfig.model_validate(config).model_dump()

    async def run(self) -> None:

        async def receive(payload: dict[str, Any]) -> None:
            event = await parse_event(payload, self.client.config.app_id, self.media)
            if event is not None:
                await self.sink(event)

        try:
            await self.connection.run(receive)
        finally:
            await self.client.http.aclose()

    async def close(self) -> None:
        await self.connection.close()
        await self.client.http.aclose()

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        return await send_message(self.client, self.media, message)

    async def probe(self) -> dict[str, Any]:
        value = await self.client.request("GET", "/users/@me")
        return {"authenticated": True, "account_id": str(value["id"])}
