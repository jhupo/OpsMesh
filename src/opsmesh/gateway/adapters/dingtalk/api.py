"""Strict OpenAPI transport; native convenience helpers swallow delivery failures."""

import asyncio
import base64
import binascii
import re
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

import httpx
from opsmesh_sdk.channels import OperationRejected
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class APIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str
    api: Literal["modern", "legacy"] = "modern"
    query: dict[str, str | int | float | bool] = Field(default_factory=dict)
    body: dict[str, Any] | list[Any] | None = None


class MediaUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["image", "voice", "video", "file"]
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1, max_length=8 * 1024 * 1024)


class DingTalkAPI:
    def __init__(self, client: Any, http: httpx.AsyncClient):
        self.client, self.http = client, http
        self.token_lock = asyncio.Lock()

    async def token(self) -> str:
        async with self.token_lock:
            token = await asyncio.to_thread(self.client.get_access_token)
        if not token:
            raise OperationRejected("dingtalk_token_unavailable")
        return str(token)

    @staticmethod
    def response(response: httpx.Response, *, webhook: bool = False) -> dict[str, Any]:
        if 400 <= response.status_code < 500:
            raise OperationRejected(f"dingtalk_http_{response.status_code}")
        response.raise_for_status()
        if response.status_code == 204:
            return {}
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("DingTalk returned a non-object response")
        if webhook and "errcode" not in data:
            raise ValueError("DingTalk webhook returned no delivery status")
        error = data.get("errcode", data.get("code"))
        if error not in {None, 0, "0", "OK", "ok", "success", "Success"}:
            code = str(error)
            if not re.fullmatch(r"[\w.-]{1,80}", code, flags=re.ASCII):
                code = "rejected"
            raise OperationRejected(f"dingtalk_{code}")
        if data.get("success") is False:
            raise OperationRejected("dingtalk_rejected")
        return data

    async def request(self, parameters: dict[str, Any]) -> dict[str, Any]:
        try:
            request = APIRequest.model_validate(parameters)
            parsed = urlsplit(request.path)
            decoded = unquote(request.path)
            if (
                not request.path.startswith("/")
                or request.path.startswith("//")
                or parsed.scheme
                or parsed.netloc
                or parsed.query
                or parsed.fragment
                or any(part in {".", ".."} for part in decoded.split("/"))
                or "\\" in decoded
                or any(ord(char) < 33 for char in decoded)
                or any(
                    key.lower().replace("-", "_")
                    in {"access_token", "accesstoken", "authorization", "appsecret", "app_secret"}
                    for key in request.query
                )
            ):
                raise ValueError("Invalid API path or auth override")
        except (ValidationError, ValueError) as exc:
            raise OperationRejected("invalid_dingtalk_request") from exc
        token = await self.token()
        query: dict[str, Any] = dict(request.query)
        if request.api == "modern":
            base, headers = "https://api.dingtalk.com", {"x-acs-dingtalk-access-token": token}
        else:
            base, headers = "https://oapi.dingtalk.com", {}
            query["access_token"] = token
        response = await self.http.request(
            request.method, base + request.path, params=query, json=request.body, headers=headers
        )
        return self.response(response)

    async def upload(self, parameters: dict[str, Any]) -> dict[str, Any]:
        try:
            request = MediaUpload.model_validate(parameters)
            if (
                "/" in request.filename
                or "\\" in request.filename
                or request.filename in {".", ".."}
            ):
                raise ValueError("Invalid media filename")
            content = base64.b64decode(request.content_base64, validate=True)
        except (ValidationError, ValueError, TypeError, binascii.Error) as exc:
            raise OperationRejected("invalid_dingtalk_media") from exc
        response = await self.http.post(
            "https://oapi.dingtalk.com/media/upload",
            params={"access_token": await self.token(), "type": request.type},
            files={"media": (request.filename, content)},
        )
        data = self.response(response)
        if not data.get("media_id"):
            raise ValueError("DingTalk upload returned no media ID")
        return data
