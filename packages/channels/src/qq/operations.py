"""QQ group and guild operations absent from the native SDK."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote, urlencode

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


class QQOperations:
    """Expose group and guild operations missing from the native SDK."""

    operations = frozenset(
        {
            "get_guild",
            "get_guild_member",
            "get_guild_members",
            "get_guild_roles",
            "mute_member",
            "unmute_member",
            "mute_all",
            "unmute_all",
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
        api = self.client.account(self.channel, self.secret)
        if params:
            path += "?" + urlencode(params)
        result = await api.request(
            method, path, body, timeout=self.channel.protocol.request_timeout
        )
        return result or None

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

    async def get_guild(self, guild_id: str) -> JsonValue:
        """Read guild information."""
        return await self._request("GET", f"/guilds/{quote(guild_id, safe='')}")

    async def get_guild_member(self, guild_id: str, user_id: str) -> JsonValue:
        """Read one guild member."""
        return await self._request(
            "GET",
            f"/guilds/{quote(guild_id, safe='')}/members/"
            f"{quote(user_id, safe='')}",
        )

    async def get_guild_members(
        self, guild_id: str, *, after: str = "0", limit: int = 100
    ) -> JsonValue:
        """Read one native guild member page."""
        if not 1 <= limit <= 400:
            raise ValueError("QQ member limit must be between 1 and 400")
        return await self._request(
            "GET",
            f"/guilds/{quote(guild_id, safe='')}/members",
            params={"after": after, "limit": limit},
        )

    async def get_guild_roles(self, guild_id: str) -> JsonValue:
        """Read guild role definitions."""
        return await self._request(
            "GET", f"/guilds/{quote(guild_id, safe='')}/roles"
        )

    async def mute_member(
        self, guild_id: str, user_id: str, *, mute_seconds: int
    ) -> JsonValue:
        """Mute a guild member for the requested duration."""
        if mute_seconds < 0:
            raise ValueError("Mute seconds must not be negative")
        return await self._request(
            "PATCH",
            f"/guilds/{quote(guild_id, safe='')}/members/"
            f"{quote(user_id, safe='')}/mute",
            body={"mute_seconds": str(mute_seconds)},
        )

    async def unmute_member(self, guild_id: str, user_id: str) -> JsonValue:
        """Clear a guild member mute."""
        return await self.mute_member(guild_id, user_id, mute_seconds=0)

    async def mute_all(self, guild_id: str, *, mute_seconds: int) -> JsonValue:
        """Mute non-administrator guild members."""
        if mute_seconds < 0:
            raise ValueError("Mute seconds must not be negative")
        return await self._request(
            "PATCH",
            f"/guilds/{quote(guild_id, safe='')}/mute",
            body={"mute_seconds": str(mute_seconds)},
        )

    async def unmute_all(self, guild_id: str) -> JsonValue:
        """Clear the guild-wide mute."""
        return await self.mute_all(guild_id, mute_seconds=0)
