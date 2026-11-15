"""QQ guild operations from the official Bot API v2 contract."""

from typing import TYPE_CHECKING
from urllib.parse import quote

from pydantic import JsonValue

from .client import QQClient

if TYPE_CHECKING:
    from .channel import QQChannel


class QQGuildOperations:
    """Operate on QQ channels identified by a guild ID."""

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
        }
    )

    def __init__(self, client: QQClient, channel: "QQChannel", secret: str):
        """Borrow an existing account to issue guild requests."""
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
