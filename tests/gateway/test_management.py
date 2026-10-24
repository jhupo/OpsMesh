"""Typed management contracts and QQ wire behavior, without live services."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from opsmesh_sdk.channel_events import ConversationEvent
from opsmesh_sdk.channel_management import (
    ConversationRef,
    ListQuery,
    ManagementRejected,
    ManagementUncertain,
    MemberAction,
    MuteMember,
    RecallMessage,
    ReviewJoinRequest,
)
from opsmesh_sdk.channels import DeliveryRejected, OutboundMessage
from opsmesh_sdk.messages import MessageFilterResult
from pydantic import ValidationError

from opsmesh.gateway.adapters.qq.client import QQClient
from opsmesh.gateway.adapters.qq.config import QQConfig
from opsmesh.gateway.adapters.qq.management import QQManagement
from opsmesh.gateway.contracts import GatewayReply


def conversation():
    return ConversationRef(scope="app", kind="group", external_id="group/id")


def client():
    return Mock(config=SimpleNamespace(app_id="app"), request=AsyncMock(return_value={}))


def test_messages_cannot_encode_management_effects():
    with pytest.raises(ValidationError):
        GatewayReply.parse({"action": "recall", "content": []})
    with pytest.raises(ValidationError):
        OutboundMessage(delivery_id=uuid4(), address={}, content=[], event="recall")
    with pytest.raises(ValidationError):
        MessageFilterResult(action="recall")
    with pytest.raises(ValidationError):
        MessageFilterResult(
            action="allow", operations=[RecallMessage(conversation=conversation(), message_id="id")]
        )


@pytest.mark.asyncio
async def test_recall_encodes_the_authorized_reference():
    transport = client()
    result = await QQManagement(transport).execute(
        RecallMessage(conversation=conversation(), message_id="original/id")
    )
    transport.request.assert_awaited_once_with(
        "DELETE", "/v2/groups/group%2Fid/messages/original%2Fid", sending=True
    )
    assert result.data["message_id"] == "original/id"


@pytest.mark.asyncio
async def test_qq_empty_success_is_confirmed_without_json():
    transport = QQClient(QQConfig(app_id="app", app_secret="secret"))
    await transport.http.aclose()
    transport.access_token = AsyncMock(return_value="test-only")
    transport.http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b""))
    )
    try:
        assert await transport.request("DELETE", "/message", sending=True) == {}
    finally:
        await transport.http.aclose()


@pytest.mark.asyncio
async def test_qq_whitelist_rejection_is_distinct_from_uncertainty():
    transport = client()
    transport.request.side_effect = DeliveryRejected("Rejected", code="11253")
    with pytest.raises(ManagementRejected, match="platform_whitelist_required"):
        await QQManagement(transport).execute(
            MemberAction(action="remove_member", conversation=conversation(), member_id="member")
        )
    transport.request.side_effect = None
    transport.request.return_value = {}
    with pytest.raises(ManagementUncertain):
        await QQManagement(transport).execute(
            MemberAction(action="remove_member", conversation=conversation(), member_id="member")
        )


@pytest.mark.asyncio
async def test_qq_mute_uses_an_absolute_expiry_and_updates_existing_restrictions():
    transport = client()
    transport.request.side_effect = [{"members": [{"member_openid": "member"}]}, {}]
    expiry = datetime.now(UTC) + timedelta(minutes=5)
    await QQManagement(transport).execute(
        MuteMember(conversation=conversation(), member_id="member", expires_at=expiry)
    )
    assert transport.request.await_args.args == (
        "POST",
        "/v2/groups/group%2Fid/restrict_chat_setting",
        {
            "members": [
                {"op": "update", "member_openid": "member", "mute_expire_at": expiry.isoformat()}
            ]
        },
    )


@pytest.mark.asyncio
async def test_member_queries_preserve_vendor_cursor_without_truncating_results():
    transport = client()
    transport.request.return_value = {
        "members": [{"member_openid": "m", "member_role": "admin"}],
        "next_cursor": "next/page",
    }
    result = await QQManagement(transport).execute(
        ListQuery(action="members", conversation=conversation(), cursor="previous/page")
    )
    assert result.data["members"][0]["role"] == "admin"
    assert result.data["next_cursor"] == "next/page"
    assert transport.request.await_args.args[1].endswith("cursor=previous%2Fpage")


def test_join_events_and_blacklist_effects_have_explicit_subjects():
    with pytest.raises(ValidationError):
        ConversationEvent(
            event_id="event",
            kind="join_requested",
            conversation=conversation(),
            occurred_at=datetime.now(UTC),
        )
    with pytest.raises(ValidationError):
        ReviewJoinRequest(
            conversation=conversation(),
            member_id="m",
            request_id="r",
            decision="approve",
            blacklist=True,
        )
