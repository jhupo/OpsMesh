"""Ordinary QQ group operations from the Bot API v2 contract."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .client import QQClient

if TYPE_CHECKING:
    from .channel import QQChannel


class QQGroupMuteChange(BaseModel):
    """One member mute change accepted by the group restriction endpoint."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True
    )

    operation: Literal["add", "update", "del"] = Field(alias="op")
    member_openid: str = Field(min_length=1, max_length=128)
    mute_expire_at: str | None = None


class QQGroupOperations:
    """Operate on ordinary QQ groups identified by a group openid."""

    operations = frozenset(
        {
            "get_group_info",
            "get_group_bot_state",
            "get_group_members",
            "get_group_member",
            "get_group_restrict_chat_setting",
            "set_group_restrict_chat_setting",
            "get_group_join_request_list",
            "approve_group_join_request",
        }
    )

    def __init__(self, client: QQClient, channel: "QQChannel", secret: str):
        """Borrow one declared account without creating network resources."""
        self.client = client
        self.channel = channel
        self.secret = secret

    async def _request(
        self, method: str, path: str, *, body=None, params=None
    ) -> JsonValue:
        token = await self.client.access_token(
            self.channel.self_id, self.channel.settings.app_id, self.secret
        )
        base = (
            self.channel.protocol.sandbox_api_base
            if self.channel.settings.sandbox
            else self.channel.protocol.api_base
        )
        return await self.client.request_json(
            method, base + path, token=token, body=body, params=params
        )

    @staticmethod
    def _group_path(group_openid: str, suffix: str = "") -> str:
        """Build a path scoped to one ordinary QQ group."""
        if not group_openid.strip():
            raise ValueError("QQ group openid must not be empty")
        return f"/v2/groups/{quote(group_openid, safe='')}{suffix}"

    async def get_group_info(self, group_openid: str) -> JsonValue:
        """Read ordinary group information."""
        return await self._request(
            "GET", self._group_path(group_openid, "/info")
        )

    async def get_group_bot_state(self, group_openid: str) -> JsonValue:
        """Read the bot's state in an ordinary group."""
        return await self._request(
            "GET", self._group_path(group_openid, "/bot_state")
        )

    async def get_group_members(
        self, group_openid: str, *, cursor: str | None = None, limit: int = 100
    ) -> JsonValue:
        """Read one ordinary group member page when the app is entitled."""
        if not 1 <= limit <= 100:
            raise ValueError("QQ group member limit must be between 1 and 100")
        params: dict[str, str | int] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return await self._request(
            "GET",
            self._group_path(group_openid, "/members"),
            params=params,
        )

    async def get_group_member(
        self, group_openid: str, member_openid: str
    ) -> JsonValue:
        """Read one ordinary group member when the app is entitled."""
        if not member_openid.strip():
            raise ValueError("QQ member openid must not be empty")
        return await self._request(
            "GET",
            self._group_path(
                group_openid, f"/members/{quote(member_openid, safe='')}"
            ),
        )

    async def get_group_restrict_chat_setting(
        self, group_openid: str
    ) -> JsonValue:
        """Read group mute state and active member restrictions."""
        return await self._request(
            "GET", self._group_path(group_openid, "/restrict_chat_setting")
        )

    async def set_group_restrict_chat_setting(
        self,
        group_openid: str,
        *,
        members: Sequence[QQGroupMuteChange],
    ) -> JsonValue:
        """Apply member mute or unmute changes in one group request."""
        if not members or len(members) > 20:
            raise ValueError(
                "QQ group mute changes must contain 1 to 20 members"
            )
        changes = [
            QQGroupMuteChange.model_validate(member).model_dump(
                mode="json", by_alias=True
            )
            for member in members
        ]
        return await self._request(
            "POST",
            self._group_path(group_openid, "/restrict_chat_setting"),
            body={"members": changes},
        )

    async def get_group_join_request_list(
        self,
        group_openid: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> JsonValue:
        """Read pending group join requests using the native cursor API."""
        if not 1 <= limit <= 100:
            raise ValueError("QQ join request limit must be between 1 and 100")
        params: dict[str, str | int] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return await self._request(
            "GET",
            self._group_path(group_openid, "/join_request_list"),
            params=params,
        )

    async def approve_group_join_request(
        self,
        group_openid: str,
        member_openid: str,
        *,
        approve: bool,
        blacklist: bool = False,
        reason: str | None = None,
    ) -> JsonValue:
        """Approve or decline one pending group join request."""
        if not member_openid.strip():
            raise ValueError("QQ member openid must not be empty")
        if reason is not None and len(reason) > 200:
            raise ValueError("QQ join request reason is too long")
        body: dict[str, JsonValue] = {
            "action": "approve" if approve else "decline",
            "blacklist": blacklist,
        }
        if reason is not None:
            body["reason"] = reason
        return await self._request(
            "POST",
            self._group_path(
                group_openid,
                f"/approval_join_request/{quote(member_openid, safe='')}",
            ),
            body=body,
        )
