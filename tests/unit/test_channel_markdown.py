"""Verify explicit formatting across bot replies, HTTP and SSE."""

import json
from types import SimpleNamespace

import httpx
import pytest

from opsmesh.plugins.channels import (
    Attachment,
    AttachmentCollection,
    Message,
    MessageSegment,
    StreamMessageEnd,
    StreamMessageStart,
    StreamTextDelta,
)
from opsmesh.plugins.channels.api.output import MessageOutput
from opsmesh.plugins.channels.dingtalk.adapter import DingTalkAdapter
from opsmesh.plugins.channels.dingtalk.channel import DingTalkChannel
from opsmesh.plugins.channels.dingtalk.client import DingTalkClient
from opsmesh.plugins.channels.dingtalk.config import (
    DingTalkConnectionSettings,
    DingTalkSettings,
)
from opsmesh.plugins.channels.dingtalk.events import parse_callback
from opsmesh.plugins.channels.dingtalk.sending import DingTalkSender
from opsmesh.plugins.channels.sending import (
    OutgoingMarkdown,
    prepare,
)


def test_markdown_factory_keeps_raw_text_for_framework_rules():
    message = Message(MessageSegment.markdown("**answer**"))
    assert (message[0].type, message.extract_plain_text()) == (
        "markdown",
        "**answer**",
    )


@pytest.mark.anyio
async def test_preflight_does_not_merge_plain_text_into_markdown():
    message = Message(
        [
            MessageSegment.text("*literal*"),
            MessageSegment.markdown("**bold**", title="Answer"),
        ]
    )
    result = await prepare(
        message,
        AttachmentCollection(),
        100,
        supported=DingTalkChannel.send_types,
    )
    assert result.parts == [
        "*literal*",
        OutgoingMarkdown("**bold**", "Answer"),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("text", [" ", "x" * 11])
async def test_markdown_uses_existing_empty_and_size_checks(text):
    with pytest.raises(ValueError):
        await prepare(
            MessageSegment.markdown(text),
            AttachmentCollection(),
            10,
            supported=DingTalkChannel.send_types,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("formatted", [False, True])
async def test_dingtalk_webhook_preserves_explicit_message_type(formatted):
    event = parse_callback(
        {
            "msgtype": "text",
            "text": {"content": "question"},
            "senderId": "user",
            "msgId": "message",
            "conversationId": "group",
            "conversationType": "2",
            "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=test",
        },
        connection_id="bot",
    )
    channel = SimpleNamespace(
        send_types=DingTalkChannel.send_types,
        settings=DingTalkConnectionSettings(
            client_id="robot", client_secret_ref="SECRET"
        ),
    )
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"errcode": 0})

    content = (
        MessageSegment.markdown("**answer**", title="Answer")
        if formatted
        else "**answer**"
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        await DingTalkSender(
            DingTalkClient(DingTalkSettings(), http), channel
        ).send(event, content)
    assert sent == (
        [
            {
                "msgtype": "markdown",
                "markdown": {"title": "Answer", "text": "**answer**"},
            }
        ]
        if formatted
        else [{"msgtype": "text", "text": {"content": "**answer**"}}]
    )


@pytest.mark.anyio
@pytest.mark.parametrize("streamed", [False, True])
async def test_api_stream_retains_markdown_format(streamed):
    output = MessageOutput(
        SimpleNamespace(
            settings=SimpleNamespace(
                max_output_bytes=1024, max_output_messages=5
            )
        ),
        SimpleNamespace(
            installation_id="consumer",
            user_id="u",
            attachments=AttachmentCollection(),
        ),
    )
    output.channel.uploads = SimpleNamespace(put_many=lambda *args: None)
    frames = []

    async def emit(frame):
        frames.append(frame.body.decode())

    async def source():
        yield StreamMessageStart("a", "markdown")
        yield StreamTextDelta("a", 1, "**an")
        yield StreamTextDelta("a", 2, "swer**")
        yield StreamMessageEnd("a")

    if streamed:
        output.emit = emit
    await output.stream(source())
    if streamed:
        start = json.loads(frames[0].split("data: ")[1])
        assert start["format"] == "markdown"
    else:
        assert output.messages[0]["content"] == [
            {"type": "markdown", "text": "**answer**", "title": "Reply"}
        ]


@pytest.mark.anyio
async def test_api_complete_markdown_keeps_title():
    channel = SimpleNamespace(
        send_types=DingTalkChannel.send_types,
        settings=SimpleNamespace(max_output_bytes=1024, max_output_messages=5),
        uploads=SimpleNamespace(put_many=lambda *args: None),
    )
    event = SimpleNamespace(
        installation_id="consumer",
        user_id="user",
        attachments=AttachmentCollection(),
    )
    output = MessageOutput(channel, event)
    await output.send(MessageSegment.markdown("**answer**", title="Answer"))
    assert output.messages[0]["content"] == [
        {"type": "markdown", "text": "**answer**", "title": "Answer"}
    ]


@pytest.mark.anyio
async def test_dingtalk_mixed_markdown_and_file_selects_native_templates(
    monkeypatch,
):
    monkeypatch.setenv("SECRET", "secret")
    event = parse_callback(
        {
            "msgtype": "text",
            "text": {"content": "question"},
            "senderId": "user",
            "msgId": "message",
            "conversationId": "group",
            "conversationType": "2",
        },
        connection_id="bot",
    )
    attachment = event.attachments.add_bytes(
        Attachment.create("report.txt", "text/plain"), b"report"
    )
    message = Message(
        [
            MessageSegment.markdown("**report**"),
            MessageSegment.attachment(attachment),
        ]
    )
    channel = SimpleNamespace(
        send_types=DingTalkChannel.send_types,
        settings=DingTalkConnectionSettings(
            client_id="robot", client_secret_ref="SECRET"
        ),
    )
    sent = []

    def respond(request):
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "token"})
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"media_id": "file"})
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"processQueryKey": "receipt"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        await DingTalkSender(
            DingTalkClient(DingTalkSettings(), http), channel
        ).send(event, message)
    assert [item["msgKey"] for item in sent] == [
        "sampleMarkdown",
        "sampleFile",
    ]
    assert json.loads(sent[0]["msgParam"]) == {
        "title": "Reply",
        "text": "**report**",
    }


@pytest.mark.anyio
async def test_dingtalk_stream_without_template_rejects_incremental_output():
    adapter = object.__new__(DingTalkAdapter)
    event = parse_callback(
        {
            "msgtype": "text",
            "text": {"content": "question"},
            "senderId": "user",
            "msgId": "message",
            "conversationId": "group",
            "conversationType": "2",
        },
        connection_id="bot",
    )
    channel = SimpleNamespace(
        send_types=DingTalkChannel.send_types,
        self_id="bot",
        ready=True,
        replies_available=True,
        settings=DingTalkConnectionSettings(
            client_id="robot", client_secret_ref="SECRET"
        ),
    )
    adapter._declared = {"bot": channel}
    sent = []
    closed = []

    async def source():
        try:
            yield StreamMessageStart("a", "markdown")
            yield StreamTextDelta("a", 1, "**one")
            yield StreamTextDelta("a", 2, " two**")
            yield StreamMessageEnd("a")
        finally:
            closed.append(True)

    async def send(channel, event, message, **options):
        assert closed == []
        sent.append(message)

    adapter._send_message = send
    from opsmesh.plugins.channels import UnsupportedOperation

    with pytest.raises(UnsupportedOperation):
        await adapter._call(channel, "stream", event=event, source=source())
    assert sent == []
    assert closed == [True]


pytestmark = pytest.mark.usefixtures("dingtalk_sdk_transport")
