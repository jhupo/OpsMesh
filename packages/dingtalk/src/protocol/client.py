"""Cancellable DingTalk API requests and response validation."""

from __future__ import annotations

import time
from collections.abc import Awaitable
from typing import Any

import aiohttp
import anyio
import httpx
from alibabacloud_dingtalk.card_1_0.client import Client as CardClient
from alibabacloud_dingtalk.im_1_0.client import Client as IMClient
from alibabacloud_dingtalk.oauth2_1_0.client import Client as OAuthClient
from alibabacloud_dingtalk.oauth2_1_0.models import GetAccessTokenRequest
from alibabacloud_dingtalk.robot_1_0.client import Client as RobotClient
from alibabacloud_tea_openapi.models import Config
from alibabacloud_tea_util.models import RuntimeOptions
from Tea.exceptions import TeaException, UnretryableException
from Tea.model import TeaModel
from yarl import URL

from ..attachments import Attachment
from ..operations import OperationError, operation_error, operation_response
from .config import DingTalkConnectionSettings
from .errors import DingTalkSendError

_TOKEN_REFRESH_MARGIN = 60


class DingTalkClient:
    """Own one connection's API clients, token cache and HTTP transport."""

    def __init__(
        self,
        settings: DingTalkConnectionSettings,
        secret: str,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        """Bind the account to its credentials and transport."""
        config = Config(protocol="https", region_id="central")
        self.oauth = OAuthClient(config)
        self.robot = RobotClient(config)
        self.im = IMClient(config)
        self.cards = CardClient(config)
        self.options = RuntimeOptions(
            autoretry=False,
            read_timeout=int(settings.request_timeout * 1000),
            connect_timeout=int(settings.request_timeout * 1000),
        )
        self.settings = settings
        self._secret = secret
        self._http = http or httpx.AsyncClient(trust_env=False)
        self._owns_http = http is None
        self._token: tuple[str, float] | None = None
        self._token_lock = anyio.Lock()

    async def close(self) -> None:
        """Discard cached credentials and close an owned transport."""
        self._token = None
        if self._owns_http:
            await self._http.aclose()

    async def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Call an official endpoint and validate its JSON object response."""
        parsed = URL(url)
        if parsed.scheme != "https" or not parsed.host:
            raise DingTalkSendError("The DingTalk endpoint is invalid")
        try:
            response = await self._http.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.settings.request_timeout,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise OperationError(0, "transport_error") from None
        return self._response(response)

    async def access_token(self) -> str:
        """Reuse the connection token and coalesce concurrent refreshes."""
        async with self._token_lock:
            cached = self._token
            if cached is not None and time.monotonic() < cached[1]:
                return cached[0]
            result = await self.invoke(
                self.oauth.get_access_token_with_options_async(
                    GetAccessTokenRequest(
                        app_key=self.settings.client_id,
                        app_secret=self._secret,
                    ),
                    {},
                    self.options,
                )
            )
            token = result.get("accessToken")
            if not isinstance(token, str) or not token:
                raise DingTalkSendError("DingTalk authentication failed")
            lifetime = result.get("expireIn")
            if isinstance(lifetime, (int, float)) and lifetime > 0:
                remaining = max(
                    0, lifetime - min(_TOKEN_REFRESH_MARGIN, lifetime / 10)
                )
                self._token = (token, time.monotonic() + remaining)
            return token

    async def invoke(self, operation: Awaitable[TeaModel]) -> dict[str, Any]:
        """Await the SDK and expose only safe error identifiers."""
        try:
            with anyio.fail_after(self.settings.request_timeout):
                response = await operation
        except TeaException as error:
            if isinstance(error, UnretryableException) and not error.code:
                raise OperationError(0, "transport_error") from None
            data = error.data or {}
            status = data.get("statusCode", 0)
            raise operation_error(
                status,
                error.code,
                data.get("requestId"),
            ) from None
        except (aiohttp.ClientError, httpx.HTTPError, TimeoutError, OSError):
            raise OperationError(0, "transport_error") from None
        body = response.body
        return body.to_map() if body is not None else {}

    async def upload_media(
        self,
        token: str,
        attachment: Attachment,
        content: bytes,
        *,
        kind: str,
    ) -> str:
        """Upload through the legacy endpoint absent from the service SDK."""
        try:
            response = await self._http.post(
                "https://oapi.dingtalk.com/media/upload",
                params={"access_token": token},
                data={"type": kind},
                files={
                    "media": (attachment.name, content, attachment.media_type)
                },
                timeout=self.settings.request_timeout,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise OperationError(0, "transport_error") from None
        result = self._response(response)
        media_id = result.get("media_id")
        if not isinstance(media_id, str) or not media_id:
            raise DingTalkSendError(
                "DingTalk upload response is missing media_id"
            )
        return media_id

    @staticmethod
    def _response(response: httpx.Response) -> dict[str, Any]:
        data = operation_response(response)
        if not isinstance(data, dict):
            raise OperationError(response.status_code, "invalid_response")
        return data
