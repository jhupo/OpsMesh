"""QQ OAuth and REST. Sending errors never include platform payloads or tokens."""

import asyncio
import time
from typing import Any

import httpx
from opsmesh_sdk.channels import DeliveryRejected, DeliveryUncertain

from opsmesh.gateway.adapters.qq.config import QQConfig


class QQClient:
    def __init__(self, config: QQConfig) -> None:
        self.config = config
        self.http = httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False)
        self.base = (
            "https://sandbox.api.sgroup.qq.com" if config.sandbox else "https://api.sgroup.qq.com"
        )
        self.token = ""
        self.expires = 0.0
        self.lock = asyncio.Lock()

    async def access_token(self) -> str:
        async with self.lock:
            if time.monotonic() >= self.expires:
                response = await self.http.post(
                    "https://bots.qq.com/app/getAppAccessToken",
                    json={"appId": self.config.app_id, "clientSecret": self.config.app_secret},
                )
                if response.status_code != 200:
                    raise DeliveryRejected("QQ authentication rejected")
                value = response.json()
                self.token = value["access_token"]
                self.expires = time.monotonic() + max(1, int(value["expires_in"]) - 60)
            return self.token

    async def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, sending: bool = False
    ) -> dict[str, Any]:
        token = await self.access_token()
        try:
            response = await self.http.request(
                method,
                self.base + path,
                headers={"Authorization": "QQBot " + token, "X-Union-Appid": self.config.app_id},
                json=body,
            )
        except httpx.HTTPError as error:
            if sending:
                raise DeliveryUncertain("QQ delivery outcome is unknown") from error
            raise
        if response.status_code == 401:
            self.expires = 0
            raise DeliveryRejected("QQ authentication expired", retryable=True)
        if response.status_code == 429:
            raise DeliveryRejected("QQ rate limited", retryable=True)
        if response.status_code >= 500 and sending:
            raise DeliveryUncertain("QQ server delivery outcome is unknown")
        if response.status_code >= 300:
            raise DeliveryRejected("QQ API rejected request")
        if response.status_code == 204:
            return {}
        try:
            value: dict[str, Any] = response.json()
        except ValueError as error:
            raise DeliveryUncertain("QQ response could not be confirmed") from error
        return value
