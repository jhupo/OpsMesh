"""DingTalk IM operations using the native asynchronous SDK."""

from typing import TYPE_CHECKING

from alibabacloud_dingtalk.im_1_0 import models
from pydantic import JsonValue

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

    def __init__(self, channel: "DingTalkChannel"):
        """Borrow the existing connection transport and credentials."""
        self.channel = channel
        self.client = channel.client

    async def query_open_group_base_info(
        self, open_conversation_id: str
    ) -> JsonValue:
        """Read group base information available to the application."""
        token = await self.client.access_token()
        return await self.client.invoke(
            self.client.im.query_open_group_base_info_with_options_async(
                models.QueryOpenGroupBaseInfoRequest(
                    open_conversation_id=open_conversation_id
                ),
                models.QueryOpenGroupBaseInfoHeaders(
                    x_acs_dingtalk_access_token=token
                ),
                self.client.options,
            )
        )

    async def query_group_member(self, open_conversation_id: str) -> JsonValue:
        """Query interconnection group members using their native ID scope."""
        token = await self.client.access_token()
        return await self.client.invoke(
            self.client.im.query_group_member_with_options_async(
                models.QueryGroupMemberRequest(
                    open_conversation_id=open_conversation_id
                ),
                models.QueryGroupMemberHeaders(
                    x_acs_dingtalk_access_token=token
                ),
                self.client.options,
            )
        )

    async def update_group_name(
        self, open_conversation_id: str, group_name: str
    ) -> JsonValue:
        """Rename an interconnection group when the app has permission."""
        if not group_name.strip():
            raise ValueError("Group name must not be empty")
        token = await self.client.access_token()
        return await self.client.invoke(
            self.client.im.update_group_name_with_options_async(
                models.UpdateGroupNameRequest(
                    open_conversation_id=open_conversation_id,
                    group_name=group_name,
                ),
                models.UpdateGroupNameHeaders(
                    x_acs_dingtalk_access_token=token
                ),
                self.client.options,
            )
        )

    async def add_group_member(
        self, open_conversation_id: str, operator_id: str, user_ids: list[str]
    ) -> JsonValue:
        """Add organization users to an interconnection group."""
        if not user_ids or not all(user_ids):
            raise ValueError("Group members must not be empty")
        token = await self.client.access_token()
        return await self.client.invoke(
            self.client.im.add_group_member_with_options_async(
                models.AddGroupMemberRequest(
                    open_conversation_id=open_conversation_id,
                    operator_id=operator_id,
                    user_ids=user_ids,
                ),
                models.AddGroupMemberHeaders(x_acs_dingtalk_access_token=token),
                self.client.options,
            )
        )

    async def remove_group_member(
        self, open_conversation_id: str, operator_id: str, user_ids: list[str]
    ) -> JsonValue:
        """Remove organization users from an interconnection group."""
        if not user_ids or not all(user_ids):
            raise ValueError("Group members must not be empty")
        token = await self.client.access_token()
        return await self.client.invoke(
            self.client.im.remove_group_member_with_options_async(
                models.RemoveGroupMemberRequest(
                    open_conversation_id=open_conversation_id,
                    operator_id=operator_id,
                    user_ids=user_ids,
                ),
                models.RemoveGroupMemberHeaders(
                    x_acs_dingtalk_access_token=token
                ),
                self.client.options,
            )
        )
