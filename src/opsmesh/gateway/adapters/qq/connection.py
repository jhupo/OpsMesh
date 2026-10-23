"""QQ WebSocket lifecycle, heartbeat acknowledgements and durable sequence progress."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import websockets

from opsmesh.gateway.adapters.qq.client import QQClient


class QQConnection:
    def __init__(self, client: QQClient) -> None:
        self.client = client
        self.session_id: str | None = None
        self.sequence: int | None = None
        self.socket: Any = None

    async def run(self, receive: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        delay = 1
        while True:
            try:
                value = await self.client.request("GET", "/gateway/bot")
                url = str(value["url"])
                parsed = urlparse(url)
                if parsed.scheme != "wss" or not (parsed.hostname or "").endswith(".qq.com"):
                    raise ValueError("Invalid QQ gateway endpoint")
                async with websockets.connect(url, max_size=1_000_000, open_timeout=20) as ws:
                    self.socket = ws
                    hello = json.loads(await asyncio.wait_for(ws.recv(), 20))
                    if hello.get("op") != 10:
                        raise ConnectionError("QQ hello missing")
                    interval = max(1, hello["d"]["heartbeat_interval"] / 1000)
                    token = "QQBot " + await self.client.access_token()
                    auth = (
                        {"token": token, "session_id": self.session_id, "seq": self.sequence}
                        if self.session_id
                        else {
                            "token": token,
                            "intents": self.client.config.intents,
                            "shard": [0, 1],
                        }
                    )
                    await ws.send(json.dumps({"op": 6 if self.session_id else 2, "d": auth}))
                    acknowledged = asyncio.Event()
                    acknowledged.set()

                    async def heartbeat(
                        acknowledged: asyncio.Event = acknowledged, interval: float = interval
                    ) -> None:
                        while True:
                            if not acknowledged.is_set():
                                await ws.close()
                                return
                            acknowledged.clear()
                            await ws.send(json.dumps({"op": 1, "d": self.sequence}))
                            await asyncio.sleep(interval)

                    task = asyncio.create_task(heartbeat())
                    try:
                        async for raw in ws:
                            payload = json.loads(raw)
                            op = payload.get("op")
                            if op == 11:
                                acknowledged.set()
                            elif op in {7, 9}:
                                if op == 9 and not payload.get("d"):
                                    self.session_id, self.sequence, self.client.expires = (
                                        None,
                                        None,
                                        0,
                                    )
                                break
                            elif op == 0:
                                if payload.get("t") == "READY":
                                    self.session_id = payload["d"]["session_id"]
                                    delay = 1
                                elif payload.get("t") == "RESUMED":
                                    delay = 1
                                else:
                                    await receive(payload)
                                # Advance after durable receipt or an explicitly ignored event.
                                self.sequence = payload.get("s", self.sequence)
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "QQ connection will retry: %s",
                    type(error).__name__,
                )
            finally:
                self.socket = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def close(self) -> None:
        if self.socket is not None:
            await self.socket.close()
