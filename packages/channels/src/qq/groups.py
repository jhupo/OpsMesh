"""QQ guild operations from the official botpy API contract."""

from typing import TYPE_CHECKING
from urllib.parse import quote

from pydantic import JsonValue

from .client import QQClient

if TYPE_CHECKING:
    from .channel import QQChannel


class QQGroups:
    """Borrow an existing account to operate on guild IDs, not group openids."""

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
            "get_delete_member",
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

    async def get_guild(self, guild_id: str) -> JsonValue:
        """Read guild information; requires access to the specified guild."""
        return await self._request("GET", f"/guilds/{quote(guild_id, safe='')}")

    async def get_guild_member(self, guild_id: str, user_id: str) -> JsonValue:
        """Read one guild member using native QQ user IDs."""
        return await self._request(
            "GET",
            f"/guilds/{quote(guild_id, safe='')}/members/"
            f"{quote(user_id, safe='')}",
        )

    async def get_guild_members(
        self, guild_id: str, *, after: str = "0", limit: int = 100
    ) -> JsonValue:
        """Read a native cursor page; private-domain permission is required."""
        if not 1 <= limit <= 400:
            raise ValueError("QQ member limit must be between 1 and 400")
        return await self._request(
            "GET",
            f"/guilds/{quote(guild_id, safe='')}/members",
            params={"after": after, "limit": limit},
        )

    async def get_guild_roles(self, guild_id: str) -> JsonValue:
        """Read the guild's role definitions."""
        return await self._request(
            "GET", f"/guilds/{quote(guild_id, safe='')}/roles"
        )

    async def mute_member(
        self, guild_id: str, user_id: str, *, mute_seconds: int
    ) -> JsonValue:
        """Mute a guild member; the bot requires administrator permission."""
        if mute_seconds < 0:
            raise ValueError("Mute seconds must not be negative")
        return await self._request(
            "PATCH",
            f"/guilds/{quote(guild_id, safe='')}/members/"
            f"{quote(user_id, safe='')}/mute",
            body={"mute_seconds": str(mute_seconds)},
        )

    async def unmute_member(self, guild_id: str, user_id: str) -> JsonValue:
        """Clear a member's guild mute."""
        return await self.mute_member(guild_id, user_id, mute_seconds=0)

    async def mute_all(self, guild_id: str, *, mute_seconds: int) -> JsonValue:
        """Mute non-admin guild members for the specified duration."""
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

    async def get_delete_member(
        self,
        guild_id: str,
        user_id: str,
        *,
        add_blacklist: bool = False,
        delete_history_msg_days: int = 0,
    ) -> JsonValue:
        """Remove a guild member using the native botpy operation name."""
        if delete_history_msg_days not in (-1, 0, 3, 7, 15, 30):
            raise ValueError("Unsupported QQ history deletion window")
        return await self._request(
            "DELETE",
            f"/guilds/{quote(guild_id, safe='')}/members/"
            f"{quote(user_id, safe='')}",
            body={
                "add_blacklist": add_blacklist,
                "delete_history_msg_days": delete_history_msg_days,
            },
        )
