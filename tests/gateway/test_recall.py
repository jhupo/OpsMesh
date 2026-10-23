"""Recall cannot select a different message or inherit permission from content."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from opsmesh_sdk.channels import DeliveryRejected, OutboundMessage
from pydantic import ValidationError

from opsmesh.gateway.adapters.qq.messages import send_message
from opsmesh.gateway.contracts import GatewayReply


def recall(**address: str) -> OutboundMessage:
    return OutboundMessage(delivery_id=uuid4(), address=address, content=[], event="recall")


def test_recall_output_cannot_supply_content_or_a_target() -> None:
    assert GatewayReply.parse({"action": "recall", "content": []}).action == "recall"
    with pytest.raises(ValueError):
        GatewayReply.parse({"action": "recall", "content": [{"text": "different message"}]})
    with pytest.raises(ValidationError):
        GatewayReply.parse({"action": "recall", "content": [], "message_id": "other"})
    with pytest.raises(ValidationError):
        OutboundMessage(delivery_id=uuid4(), address={}, content=[], event="message")


@pytest.mark.asyncio
async def test_qq_recall_uses_only_the_trusted_current_reply_address() -> None:
    client = Mock(request=AsyncMock(return_value={}))
    receipt = await send_message(
        client, Mock(), recall(kind="group", target="group/id", msg_id="current/id")
    )
    client.request.assert_awaited_once_with(
        "DELETE", "/v2/groups/group%2Fid/messages/current%2Fid", sending=True
    )
    assert receipt.remote_id == "current/id"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [{"kind": "c2c", "target": "user", "msg_id": "id"}, {"kind": "group", "target": "group"}],
)
async def test_qq_recall_rejects_private_or_unidentified_messages(address: dict[str, str]) -> None:
    client = Mock(request=AsyncMock())
    with pytest.raises(DeliveryRejected):
        await send_message(client, Mock(), recall(**address))
    client.request.assert_not_awaited()
