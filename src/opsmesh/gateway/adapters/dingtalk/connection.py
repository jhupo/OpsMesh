"""Own cancellable socket lifetime; the official SDK owns frame routing and ACKs."""

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote, urlparse

import dingtalk_stream
import websockets

from opsmesh.gateway.adapters.dingtalk.client import DingTalkClient


class DingTalkConnection:
    def __init__(self, client: DingTalkClient, handler: Any) -> None:
        self.client = client
        # The upstream logger can include callback bodies and tickets. Do not expose them.
        logger = logging.Logger("opsmesh.dingtalk.protocol", level=logging.CRITICAL + 1)
        logger.addHandler(logging.NullHandler())
        self.sdk = dingtalk_stream.DingTalkStreamClient(
            dingtalk_stream.Credential(client.config.app_key, client.config.app_secret),
            logger=logger,
        )
        self.sdk.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, handler)
        self.socket: Any = None

    async def run(self) -> None:
        self.sdk.pre_start()
        delay = 1
        while True:
            try:
                value = await self.client.post(
                    "https://api.dingtalk.com/v1.0/gateway/connections/open",
                    {
                        "clientId": self.client.config.app_key,
                        "clientSecret": self.client.config.app_secret,
                        "subscriptions": [
                            {"type": "CALLBACK", "topic": dingtalk_stream.ChatbotMessage.TOPIC}
                        ],
                        "ua": "OpsMesh",
                    },
                    authenticated=False,
                )
                endpoint = str(value["endpoint"])
                parsed = urlparse(endpoint)
                if parsed.scheme != "wss" or not (parsed.hostname or "").endswith(".dingtalk.com"):
                    raise ValueError("Invalid DingTalk Stream endpoint")
                async with websockets.connect(
                    endpoint + "?ticket=" + quote(value["ticket"], safe=""),
                    max_size=1_000_000,
                    open_timeout=20,
                    ping_interval=30,
                    ping_timeout=30,
                ) as ws:
                    self.socket = self.sdk.websocket = ws
                    delay = 1
                    async for raw in ws:
                        # Callback returns success only after the sink committed its inbox row.
                        result = await self.sdk.route_message(json.loads(raw))
                        if result == self.sdk.TAG_DISCONNECT:
                            break
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "DingTalk connection will retry: %s",
                    type(error).__name__,
                )
            finally:
                self.socket = self.sdk.websocket = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def close(self) -> None:
        if self.socket is not None:
            await self.socket.close()
