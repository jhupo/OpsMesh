"""DingTalk IM operations using the official im_1.0 SDK wire contract."""

from typing import TYPE_CHECKING

from pydantic import JsonValue

from .client import DingTalkClient

if TYPE_CHECKING:
    from .channel import DingTalkChannel


class DingTalkGroups:
    """Operate on explicit group types with the existing account client."""

    operations = frozenset(
        {
            "query_open_group_base_info",
            "query_group_member",
            "update_group_name",
            "add_group_member",
            "remove_group_member",
        }
    )

    def __init__(self, client: DingTalkClient, channel: "DingTalkChannel"):
        """Borrow the existing account transport and credentials."""
        self.client = client
        self.channel = channel

    async def _request(
        self, method: str, path: str, *, body=None, params=None
    ) -> JsonValue:
        token = await self.client.access_token(self.channel.settings)
        return await self.client.request_json(
            method, path, token=token, body=body, params=params
        )

    async def query_open_group_base_info(
        self, open_conversation_id: str
    ) -> JsonValue:
        """Read group base information available to the application."""
        return await self._request(
            "POST",
            "/v1.0/im/groups/baseInfos/query",
            body={"openConversationId": open_conversation_id},
        )

    async def query_group_member(self, open_conversation_id: str) -> JsonValue:
        """Query interconnection group members using their native ID scope."""
        return await self._request(
            "GET",
            "/v1.0/im/interconnections/conversations/members",
            params={"openConversationId": open_conversation_id},
        )

    async def update_group_name(
        self, open_conversation_id: str, group_name: str
    ) -> JsonValue:
        """Rename an interconnection group when the app has permission."""
        if not group_name.strip():
            raise ValueError("Group name must not be empty")
        return await self._request(
            "PUT",
            "/v1.0/im/interconnections/groups/names",
            body={
                "openConversationId": open_conversation_id,
                "groupName": group_name,
            },
        )

    async def add_group_member(
        self, open_conversation_id: str, operator_id: str, user_ids: list[str]
    ) -> JsonValue:
        """Add organization users to an interconnection group."""
        if not user_ids or not all(user_ids):
            raise ValueError("Group members must not be empty")
        return await self._request(
            "POST",
            "/v1.0/im/interconnections/groups/members",
            body={
                "openConversationId": open_conversation_id,
                "operatorId": operator_id,
                "userIds": user_ids,
            },
        )

    async def remove_group_member(
        self, open_conversation_id: str, operator_id: str, user_ids: list[str]
    ) -> JsonValue:
        """Remove organization users from an interconnection group."""
        if not user_ids or not all(user_ids):
            raise ValueError("Group members must not be empty")
        return await self._request(
            "POST",
            "/v1.0/im/interconnections/groups/members/remove",
            body={
                "openConversationId": open_conversation_id,
                "operatorId": operator_id,
                "userIds": user_ids,
            },
        )
