"""DingTalk callbacks commit before the official SDK emits its ACK."""

import json
from typing import Any

import dingtalk_stream
from opsmesh_sdk.channels import (
    ChannelAdapter,
    ChannelCapabilities,
    DeliveryReceipt,
    OutboundMessage,
)

from opsmesh.gateway.adapters.dingtalk.client import DingTalkClient
from opsmesh.gateway.adapters.dingtalk.config import DingTalkConfig
from opsmesh.gateway.adapters.dingtalk.connection import DingTalkConnection
from opsmesh.gateway.adapters.dingtalk.events import parse_event
from opsmesh.gateway.adapters.dingtalk.messages import send_message


class DingTalkAdapter(ChannelAdapter):
    capabilities = ChannelCapabilities(inbound_media=True, outbound_media=True)

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        return DingTalkConfig.model_validate(config).model_dump()

    async def run(self) -> None:
        self.client = DingTalkClient(DingTalkConfig.model_validate(self.config))
        adapter = self

        class Handler(dingtalk_stream.CallbackHandler):  # type: ignore[misc]
            async def process(self, callback: Any) -> tuple[int, str]:
                data = (
                    json.loads(callback.data) if isinstance(callback.data, str) else callback.data
                )
                event = await parse_event(data, adapter.client, adapter.media)
                await adapter.sink(event)
                return 200, "OK"

        self.connection = DingTalkConnection(self.client, Handler())
        try:
            await self.connection.run()
        finally:
            await self.client.http.aclose()

    async def close(self) -> None:
        if hasattr(self, "connection"):
            await self.connection.close()

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        return await send_message(self.client, self.media, message)
