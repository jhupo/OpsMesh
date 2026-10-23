"""Bounded async DingTalk API calls, keeping secrets at the transport boundary."""

import asyncio
import time
from typing import Any

import httpx
from opsmesh_sdk.channels import DeliveryRejected, DeliveryUncertain

from opsmesh.gateway.adapters.dingtalk.config import DingTalkConfig


class DingTalkClient:
    def __init__(self, config: DingTalkConfig) -> None:
        self.config = config
        self.http = httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False)
        self.token = ""
        self.expires = 0.0
        self.lock = asyncio.Lock()

    async def access_token(self) -> str:
        async with self.lock:
            if time.monotonic() >= self.expires:
                response = await self.http.post(
                    "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                    json={"appKey": self.config.app_key, "appSecret": self.config.app_secret},
                )
                if response.status_code != 200:
                    raise DeliveryRejected("DingTalk authentication rejected")
                value = response.json()
                self.token = value["accessToken"]
                self.expires = time.monotonic() + max(1, int(value["expireIn"]) - 60)
            return self.token

    async def post(
        self, url: str, body: dict[str, Any], *, authenticated: bool = True, sending: bool = False
    ) -> dict[str, Any]:
        headers = (
            {"x-acs-dingtalk-access-token": await self.access_token()} if authenticated else {}
        )
        try:
            response = await self.http.post(url, json=body, headers=headers)
        except httpx.HTTPError as error:
            if sending:
                raise DeliveryUncertain("DingTalk delivery outcome is unknown") from error
            raise
        if response.status_code == 401:
            self.expires = 0
            raise DeliveryRejected("DingTalk authentication expired", retryable=True)
        if response.status_code == 429:
            raise DeliveryRejected("DingTalk rate limited", retryable=True)
        if response.status_code >= 500 and sending:
            raise DeliveryUncertain("DingTalk server delivery outcome is unknown")
        if response.status_code >= 300:
            raise DeliveryRejected("DingTalk rejected request")
        try:
            value: dict[str, Any] = response.json()
        except ValueError as error:
            raise DeliveryUncertain("DingTalk response could not be confirmed") from error
        if value.get("errcode", 0) != 0 or value.get("code"):
            raise DeliveryRejected("DingTalk API rejected request")
        return value
