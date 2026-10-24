"""QQ group management; vendor routes and identifiers stay in this adapter."""

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

from opsmesh_sdk.channel_management import (
    ActionCapability,
    ConversationManagement,
    ListQuery,
    ManagementAction,
    ManagementRejected,
    ManagementRequest,
    ManagementResult,
    ManagementUncertain,
    MemberAction,
    MemberQuery,
    MuteMember,
    RecallMessage,
    ReviewJoinRequest,
)
from opsmesh_sdk.channels import DeliveryRejected, DeliveryUncertain

from opsmesh.gateway.adapters.qq.client import QQClient

QQ_ACTIONS: frozenset[ManagementAction] = frozenset(
    {
        "conversation",
        "permissions",
        "members",
        "member",
        "restrictions",
        "join_requests",
        "recall",
        "mute",
        "unmute",
        "review_join",
        "remove_member",
        "blacklist",
        "unblacklist",
    }
)


def member(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": value["member_openid"],
        "name": value.get("username", ""),
        "role": value.get("member_role"),
        "is_bot": value.get("bot", False),
        "joined_at": value.get("joined_at"),
    }


class QQManagement(ConversationManagement):
    capabilities = tuple(
        ActionCapability(
            action=action,
            max_duration_seconds=2592000 if action == "mute" else None,
            max_message_age_seconds=120 if action == "recall" else None,
            fixed_page_size=30 if action == "members" else None,
            max_page_size=50 if action == "join_requests" else None,
            reason="Platform authorization is unknown; invitation whitelist may be required",
        )
        for action in sorted(QQ_ACTIONS)
    )

    def __init__(self, client: QQClient) -> None:
        self.client = client

    async def execute(self, request: ManagementRequest) -> ManagementResult:
        if (
            request.conversation.kind != "group"
            or request.conversation.scope != self.client.config.app_id
        ):
            raise ManagementRejected("unsupported_conversation")
        try:
            return await self.perform(request)
        except DeliveryRejected as error:
            code = {
                "11253": "platform_whitelist_required",
                "40062003": "platform_forbidden",
                "40064004": "message_expired",
                "40061001": "message_invalid",
            }.get(error.code or "", "platform_rejected" + (":" + error.code if error.code else ""))
            raise ManagementRejected(code, retryable=error.retryable) from error
        except DeliveryUncertain as error:
            raise ManagementUncertain("QQ operation outcome is unknown") from error

    async def perform(self, request: ManagementRequest) -> ManagementResult:
        base = "/v2/groups/" + quote(request.conversation.external_id, safe="")
        action = request.action
        if action == "conversation":
            value = await self.client.request("GET", base + "/info")
            return ManagementResult(
                data={
                    "id": value["group_openid"],
                    "name": value.get("group_name", ""),
                    "description": value.get("group_finger_memo", ""),
                    "tags": value.get("group_tags", []),
                    "member_count": value.get("group_member_num"),
                }
            )
        if action == "permissions":
            value = await self.client.request("GET", base + "/bot_state")
            privileged = value.get("member_role") in {"owner", "admin"}
            restricted = {"mute", "unmute", "restrictions", "join_requests", "review_join"}
            capabilities = [
                item.model_copy(
                    update={"available": False, "reason": "Bot requires a group administrator role"}
                )
                if item.action in restricted and not privileged
                else item
                for item in self.capabilities
            ]
            return ManagementResult(
                data={
                    "bot_id": value["member_openid"],
                    "role": value.get("member_role"),
                    "visibility": value.get("recv_msg_setting"),
                    "proactive": value.get("allow_proactive_msg", False),
                },
                capabilities=capabilities,
            )
        if isinstance(request, MemberQuery):
            value = await self.client.request(
                "GET", base + "/members/" + quote(request.member_id, safe="")
            )
            return ManagementResult(data=member(value))
        if isinstance(request, ListQuery):
            if action == "members":
                if request.limit != 30:
                    raise ManagementRejected("page_size_unsupported:QQ_members_pages_are_up_to_30")
                value = await self.client.request(
                    "GET", base + "/members?" + urlencode({"cursor": request.cursor})
                )
                return ManagementResult(
                    data={
                        "members": [member(item) for item in value.get("members", [])],
                        "next_cursor": value.get("next_cursor", ""),
                    }
                )
            if request.limit > 50:
                raise ManagementRejected("page_size_unsupported:maximum_50")
            value = await self.client.request(
                "GET",
                base
                + "/join_request_list?"
                + urlencode({"cursor": request.cursor, "limit": request.limit}),
            )
            return ManagementResult(
                data={
                    "requests": [
                        {
                            "id": item["join_request_id"],
                            "member_id": item["member_openid"],
                            "name": item.get("username", ""),
                            "requested_at": item.get("apply_at"),
                            "source": item.get("apply_source"),
                            "risk": item.get("risk_tips", ""),
                            "verification": item.get("verify_info", {}),
                        }
                        for item in value.get("list", [])
                    ],
                    "next_cursor": value.get("next_cursor", ""),
                }
            )
        if action == "restrictions":
            value = await self.client.request("GET", base + "/restrict_chat_setting")
            return ManagementResult(
                data={
                    "global_rule": value.get("global_rule", {}),
                    "members": [
                        {"member_id": item["member_openid"], "expires_at": item["mute_expire_at"]}
                        for item in value.get("members", [])
                    ],
                }
            )
        if isinstance(request, RecallMessage):
            await self.client.request(
                "DELETE", base + "/messages/" + quote(request.message_id, safe=""), sending=True
            )
            return ManagementResult(data={"message_id": request.message_id})
        if isinstance(request, MuteMember):
            if not 0 < (request.expires_at - datetime.now(UTC)).total_seconds() <= 2592000:
                raise ManagementRejected("mute_duration_invalid")
            current = await self.client.request("GET", base + "/restrict_chat_setting")
            operation = (
                "update"
                if any(
                    item.get("member_openid") == request.member_id
                    for item in current.get("members", [])
                )
                else "add"
            )
            await self.client.request(
                "POST",
                base + "/restrict_chat_setting",
                {
                    "members": [
                        {
                            "op": operation,
                            "member_openid": request.member_id,
                            "mute_expire_at": request.expires_at.isoformat(),
                        }
                    ]
                },
                sending=True,
            )
        elif isinstance(request, ReviewJoinRequest):
            await self.client.request(
                "POST",
                base + "/approval_join_request/" + quote(request.member_id, safe=""),
                {
                    "op": request.decision,
                    "join_request_id": request.request_id,
                    "reject_reason": request.reason if request.decision == "decline" else "",
                    "add_to_member_blacklist": request.blacklist,
                },
                sending=True,
            )
        elif isinstance(request, MemberAction) and action == "unmute":
            await self.client.request(
                "POST",
                base + "/restrict_chat_setting",
                {"members": [{"op": "del", "member_openid": request.member_id}]},
                sending=True,
            )
        elif isinstance(request, MemberAction) and action == "remove_member":
            value = await self.client.request(
                "POST",
                base + "/batch_remove_members",
                {"member_openids": [request.member_id], "add_to_member_blacklist": False},
                sending=True,
            )
            if value.get("remove_members_result") != "success":
                raise ManagementUncertain("QQ did not confirm member removal")
        elif isinstance(request, MemberAction) and action in {"blacklist", "unblacklist"}:
            value = await self.client.request(
                "POST",
                base + "/member_blacklist",
                {
                    "op": "add" if action == "blacklist" else "del",
                    "member_openids": [request.member_id],
                },
                sending=True,
            )
            if "fail_openids" not in value:
                raise ManagementUncertain("QQ did not confirm blacklist outcome")
            if request.member_id in value["fail_openids"]:
                raise ManagementRejected("member_operation_rejected")
        else:
            raise ManagementRejected("unsupported")
        return ManagementResult()
