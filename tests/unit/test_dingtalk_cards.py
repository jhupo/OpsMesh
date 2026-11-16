"""Verify card delivery, finalization and cleanup without sending messages."""

import json
from types import SimpleNamespace

import anyio
import httpx
import pytest

from opsmesh.plugins.channels.dingtalk.cards import DingTalkCardStream
from opsmesh.plugins.channels.dingtalk.client import DingTalkClient
from opsmesh.plugins.channels.dingtalk.config import (
    DingTalkConnectionSettings,
    DingTalkSettings,
)
from opsmesh.plugins.channels.dingtalk.errors import DingTalkSendError
from opsmesh.plugins.channels.dingtalk.events import parse_callback


def context(group=True):
    event = parse_callback(
        {
            "msgtype": "text",
            "text": {"content": "question"},
            "senderId": "user",
            "senderStaffId": "staff",
            "msgId": "message",
            "conversationId": "group",
            "conversationType": "2" if group else "1",
        },
        connection_id="bot",
    )
    channel = SimpleNamespace(
        settings=DingTalkConnectionSettings(
            client_id="robot",
            client_secret_ref="CARD_SECRET",
            card={"template_id": "published.schema", "update_interval": 0.5},
        ),
        protocol=DingTalkSettings(),
    )
    return event, channel


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv("CARD_SECRET", "test-secret")


class Transport:
    def __init__(self):
        self.calls = []
        self.creation = {"result": {"success": True}}

    def __call__(self, request):
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "token"})
        body = json.loads(request.content)
        self.calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("createAndDeliver"):
            return httpx.Response(200, json=self.creation)
        return httpx.Response(200, json={"success": True, "result": True})


@pytest.mark.anyio
@pytest.mark.parametrize("group", [False, True])
async def test_card_stream_uses_one_identity_and_finalizes_after_cleanup(
    group,
):
    event, channel = context(group)
    transport = Transport()
    closed = []

    async def source():
        try:
            yield "**one"
            assert len(transport.calls) == 1
            yield " two**"
        finally:
            closed.append(True)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport)
    ) as http:
        result = await DingTalkCardStream(
            DingTalkClient(channel.protocol, http), channel
        ).send(event, source())
    assert len(transport.calls) == 3
    creation, final, state = [body for _, _, body in transport.calls]
    assert creation["openSpaceId"] == (
        "dtv1.card//IM_GROUP.group" if group else "dtv1.card//IM_ROBOT.staff"
    )
    assert {body["outTrackId"] for _, _, body in transport.calls} == {
        result["outTrackId"]
    }
    assert (
        closed,
        final["content"],
        final["isFull"],
        final["isFinalize"],
        final["isError"],
        state["cardData"]["cardParamMap"]["flowStatus"],
    ) == ([True], "**one two**", True, True, False, "3")


@pytest.mark.anyio
async def test_source_failure_closes_and_marks_existing_card_failed():
    event, channel = context()
    transport = Transport()
    closed = []

    async def source():
        try:
            yield "partial"
            raise RuntimeError("model disconnected")
        finally:
            closed.append(True)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport)
    ) as http:
        with pytest.raises(RuntimeError, match="model disconnected"):
            await DingTalkCardStream(
                DingTalkClient(channel.protocol, http), channel
            ).send(event, source())
    assert closed == [True]
    assert transport.calls[-1][2]["cardData"]["cardParamMap"] == {
        "msgContent": "partial",
        "flowStatus": "5",
    }
    assert transport.calls[-2][2]["isError"] is True


@pytest.mark.anyio
async def test_cancellation_shields_cleanup_without_another_reply():
    event, channel = context()
    transport = Transport()
    closed = []

    async def source():
        try:
            yield "partial"
            await anyio.sleep_forever()
        finally:
            closed.append(True)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport)
    ) as http:
        with anyio.move_on_after(0.05) as scope:
            await DingTalkCardStream(
                DingTalkClient(channel.protocol, http), channel
            ).send(event, source())
    assert (scope.cancel_called, closed) == (True, [True])
    assert [path for _, path, _ in transport.calls] == [
        "/v1.0/card/instances/createAndDeliver",
        "/v1.0/card/streaming",
        "/v1.0/card/instances",
    ]
    assert (
        transport.calls[-1][2]["cardData"]["cardParamMap"]["flowStatus"] == "5"
    )


@pytest.mark.anyio
async def test_nested_delivery_rejection_is_not_reported_as_success():
    event, channel = context()
    transport = Transport()
    transport.creation = {"result": [{"success": False}]}

    async def source():
        yield "answer"
        pytest.fail("Rejected delivery must stop consuming the model")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport)
    ) as http:
        with pytest.raises(DingTalkSendError, match="rejected"):
            await DingTalkCardStream(
                DingTalkClient(channel.protocol, http), channel
            ).send(event, source())
    assert len([call for call in transport.calls if call[0] == "POST"]) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("chunks", [[], [" "], [123], ["x" * 11]])
async def test_invalid_stream_does_not_create_card(chunks):
    event, channel = context()
    channel.settings = channel.settings.model_copy(
        update={"max_message_length": 10}
    )
    transport = Transport()

    async def source():
        for chunk in chunks:
            yield chunk

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport)
    ) as http:
        with pytest.raises((ValueError, TypeError)):
            await DingTalkCardStream(
                DingTalkClient(channel.protocol, http), channel
            ).send(event, source())
    assert transport.calls == []


def test_card_content_cannot_overwrite_status():
    with pytest.raises(ValueError, match="reserved"):
        DingTalkConnectionSettings(
            client_id="robot",
            client_secret_ref="SECRET",
            card={
                "template_id": "published.schema",
                "content_key": "flowStatus",
            },
        )


@pytest.mark.anyio
async def test_card_updates_while_source_is_running_and_keeps_one_card():
    event, channel = context()
    transport = Transport()

    async def source():
        yield "one"
        await anyio.sleep(0.51)
        yield " two"
        assert transport.calls[-1][2]["isFinalize"] is False
        yield " three"

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport)
    ) as http:
        await DingTalkCardStream(
            DingTalkClient(channel.protocol, http), channel
        ).send(event, source())
    updates = [
        body
        for _, path, body in transport.calls
        if path.endswith("/streaming")
    ]
    assert [(body["content"], body["isFinalize"]) for body in updates] == [
        ("one two", False),
        ("one two three", True),
    ]


@pytest.mark.anyio
async def test_update_error_survives_failed_cleanup():
    event, channel = context()
    transport = Transport()
    closed = []

    def respond(request):
        if request.url.path.endswith("/streaming"):
            return httpx.Response(403, json={"code": "Forbidden"})
        return transport(request)

    async def source():
        try:
            yield "answer"
        finally:
            closed.append(True)

    from opsmesh.plugins.channels.operations import OperationPermissionDenied

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        with pytest.raises(OperationPermissionDenied) as caught:
            await DingTalkCardStream(
                DingTalkClient(channel.protocol, http), channel
            ).send(event, source())
    assert (closed, caught.value.status) == ([True], 403)
    assert "Card failure notification failed" in caught.value.__notes__[0]
