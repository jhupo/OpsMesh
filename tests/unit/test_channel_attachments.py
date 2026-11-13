"""Exercise attachment boundaries and protocol media conversion."""

import json
from functools import partial

import pytest

from opsmesh.plugins.channels.api.attachments import AttachmentUploads
from opsmesh.plugins.channels.api.errors import HTTPError
from opsmesh.plugins.channels.api.messages import MessageRequest, from_request
from opsmesh.plugins.channels.attachments import (
    Attachment,
    AttachmentCollection,
    AttachmentError,
    AttachmentSettings,
)
from opsmesh.plugins.channels.dingtalk.events import (
    parse_callback as dingtalk_event,
)
from opsmesh.plugins.channels.qq.events import parse_callback as qq_event


@pytest.mark.anyio
async def test_incoming_and_generated_files_have_independent_budgets():
    files = AttachmentCollection(
        AttachmentSettings(max_files=1, max_file_bytes=4, max_message_bytes=4)
    )
    incoming = files.receive_bytes(
        Attachment.create("input.txt", "text/plain"), b"1234"
    )
    outgoing = files.add_bytes(
        Attachment.create("output.txt", "text/plain"), b"5678"
    )
    assert await files.read(outgoing.id) == b"5678"
    files.validate_message([outgoing.id])
    with pytest.raises(AttachmentError, match="Too many"):
        files.validate_message([incoming.id, outgoing.id])
    with pytest.raises(AttachmentError, match="Too many"):
        files.receive_bytes(Attachment.create("extra.txt", "text/plain"), b"")
    with pytest.raises(AttachmentError, match="Too many"):
        files.add_bytes(Attachment.create("extra.txt", "text/plain"), b"x")


@pytest.mark.anyio
async def test_lazy_incoming_read_does_not_consume_generated_budget():
    files = AttachmentCollection(
        AttachmentSettings(max_file_bytes=4, max_message_bytes=4)
    )

    async def read():
        return b"1234"

    incoming = Attachment.create("input.txt", "text/plain")
    files.add(incoming, read)
    outgoing = files.add_bytes(
        Attachment.create("output.txt", "text/plain"), b"5678"
    )
    assert await files.read(incoming.id) == b"1234"
    with pytest.raises(AttachmentError, match="too large"):
        files.validate_message([incoming.id, outgoing.id])


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["capacity", "invalid_segment"])
async def test_failed_api_reply_does_not_publish_partial_uploads(failure):
    from types import SimpleNamespace

    from opsmesh.plugins.channels import Message, MessageSegment
    from opsmesh.plugins.channels.api.attachments import encode_message

    files = AttachmentCollection()
    first = files.add_bytes(Attachment.create("a.txt", "text/plain"), b"a")
    second = files.add_bytes(Attachment.create("b.txt", "text/plain"), b"b")
    cache = AttachmentUploads(AttachmentSettings(max_cache_entries=1))
    event = SimpleNamespace(
        attachments=files, installation_id="chat", user_id="user"
    )
    message = Message(
        [
            MessageSegment.attachment(first),
            MessageSegment.attachment(second)
            if failure == "capacity"
            else MessageSegment("unknown", {}),
        ]
    )
    with pytest.raises((HTTPError, AttachmentError)):
        await encode_message(SimpleNamespace(uploads=cache), event, message)
    assert not cache._uploads
    cache.put(("chat", "user"), first, b"a")
    assert cache.get(("chat", "user"), first.id).content == b"a"


@pytest.mark.anyio
async def test_qq_attachment_only_message_is_lazy_and_hides_source():
    calls = []

    async def reader(url):
        calls.append(url)
        return b"image"

    event = qq_event(
        {
            "t": "C2C_MESSAGE_CREATE",
            "d": {
                "id": "m1",
                "author": {"user_openid": "u1"},
                "attachments": [
                    {
                        "filename": "pic.png",
                        "content_type": "image/png",
                        "url": "https://gchat.qpic.cn/image?secret=token",
                    }
                ],
            },
        },
        connection_id="bot",
        reader=reader,
    )
    message = event.get_message()
    assert calls == []
    assert [part.type for part in message] == ["image"]
    assert "secret" not in json.dumps([part.data for part in message])
    key = message[0].data["attachment_id"]
    assert await event.attachments.read(key) == b"image"
    assert await event.attachments.read(key) == b"image"
    assert len(calls) == 1


@pytest.mark.anyio
async def test_dingtalk_rich_text_keeps_order_and_download_code_private():
    async def reader(code):
        assert code == "private-code"
        return b"image"

    event = dingtalk_event(
        {
            "msgtype": "richText",
            "conversationId": "group",
            "msgId": "m1",
            "senderStaffId": "u1",
            "conversationType": "2",
            "isInAtList": True,
            "content": {
                "richText": [
                    {"text": "before"},
                    {"downloadCode": "private-code"},
                    {"text": "after"},
                ]
            },
        },
        connection_id="bot",
        reader=reader,
    )
    message = event.get_message()
    assert [part.type for part in message] == ["text", "image", "text"]
    assert event.is_tome()
    assert "private-code" not in str(message)
    assert (
        await event.attachments.read(message[1].data["attachment_id"])
        == b"image"
    )


@pytest.mark.anyio
async def test_download_limits_cover_file_and_aggregate_bytes():
    files = AttachmentCollection(
        AttachmentSettings(
            max_file_bytes=4,
            max_message_bytes=6,
        )
    )

    async def four():
        return b"1234"

    first = Attachment.create("a.txt", "text/plain")
    second = Attachment.create("b.txt", "text/plain")
    files.add(first, four)
    files.add(second, four)
    assert await files.read(first.id) == b"1234"
    with pytest.raises(AttachmentError, match="too large"):
        await files.read(second.id)
    with pytest.raises(AttachmentError, match="too large"):
        files.add_bytes(Attachment.create("c.txt", "text/plain"), b"12345")


def test_upload_scope_expiry_and_cache_capacity(monkeypatch):
    import opsmesh.plugins.channels.api.attachments as uploads_module

    now = [0.0]
    monkeypatch.setattr(uploads_module.time, "monotonic", lambda: now[0])
    cache = AttachmentUploads(
        AttachmentSettings(
            ttl_seconds=2,
            max_cache_bytes=4,
            max_cache_entries=1,
        )
    )
    attachment = Attachment.create("a.txt", "text/plain", 4)
    cache.put(("agent", "alice"), attachment, b"data")
    for scope in [("agent", "bob"), ("other-agent", "alice")]:
        with pytest.raises(HTTPError, match="not found"):
            cache.get(scope, attachment.id)
    with pytest.raises(HTTPError, match="capacity"):
        cache.put(
            ("agent", "alice"), Attachment.create("b", "text/plain"), b"x"
        )
    now[0] = 3
    with pytest.raises(HTTPError, match="not found"):
        cache.get(("agent", "alice"), attachment.id)
    cache.put(("agent", "alice"), Attachment.create("b", "text/plain"), b"x")
    cache.clear()
    assert not cache._uploads


def test_api_message_references_cannot_override_metadata_or_scope():
    cache = AttachmentUploads(AttachmentSettings())
    attachment = Attachment.create("pic.png", "image/png", 3)
    cache.put(("agent", "alice"), attachment, b"png")
    payload = {
        "message": [
            {"type": "text", "text": "look"},
            {"type": "image", "attachment_id": attachment.id},
            {"type": "image", "attachment_id": attachment.id},
        ]
    }
    message = from_request(
        MessageRequest.model_validate(payload),
        AttachmentCollection(),
        partial(cache.resolve, ("agent", "alice")),
    )
    assert [part.type for part in message] == ["text", "image", "image"]
    with pytest.raises(HTTPError, match="not found"):
        from_request(
            MessageRequest.model_validate(payload),
            AttachmentCollection(),
            partial(cache.resolve, ("agent", "bob")),
        )
    payload["message"][1]["type"] = "audio"
    with pytest.raises(HTTPError, match="does not match"):
        from_request(
            MessageRequest.model_validate(payload),
            AttachmentCollection(),
            partial(cache.resolve, ("agent", "alice")),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    [
        "http://qpic.cn/image",
        "https://qpic.cn.evil.test/image",
        "https://127.0.0.1/image",
        "https://qpic.cn:8443/image",
        "https://token@qpic.cn/image",
        "https://qpic.cn:not-a-port/image",
    ],
)
async def test_vendor_download_rejects_untrusted_origins(url):
    from opsmesh.plugins.channels.attachments.download import download

    with pytest.raises(AttachmentError, match="not allowed"):
        await download(url, AttachmentSettings(), ("qpic.cn",))


@pytest.mark.anyio
async def test_download_blocks_redirects_and_streams_under_size_limit(
    monkeypatch,
):
    import httpx

    import opsmesh.plugins.channels.attachments.download as transport

    responses = [
        httpx.Response(302, headers={"location": "https://evil.test"}),
        httpx.Response(200, content=b"oversized"),
    ]
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        transport.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(lambda request: responses.pop(0)),
            **kwargs,
        ),
    )
    for pattern in ("failed", "too large"):
        with pytest.raises(AttachmentError, match=pattern):
            await transport.download(
                "https://qpic.cn/image",
                AttachmentSettings(max_file_bytes=4),
                ("qpic.cn",),
            )


@pytest.mark.parametrize("name,size", [(None, None), ("ok", "100"), ("ok", -1)])
def test_metadata_rejects_malformed_vendor_values(name, size):
    with pytest.raises(AttachmentError):
        Attachment.create(name, "application/pdf", size)


@pytest.mark.parametrize(
    "media_type", ["text/\x00plain", "图像/png", "text/", "invalid"]
)
def test_metadata_rejects_invalid_header_values(media_type):
    with pytest.raises(AttachmentError):
        Attachment.create("file", media_type)


@pytest.mark.anyio
async def test_dingtalk_media_exchanges_credentials_only_at_api(monkeypatch):
    import opsmesh.plugins.channels.dingtalk.attachments as media
    from opsmesh.plugins.channels.dingtalk.attachments import (
        DingTalkAttachments,
    )
    from opsmesh.plugins.channels.dingtalk.config import (
        DingTalkConnectionSettings,
    )

    monkeypatch.setenv("MEDIA_TEST_SECRET", "secret")
    calls = []

    from opsmesh.plugins.channels.dingtalk.client import DingTalkClient

    class Client(DingTalkClient):
        def __init__(self):
            pass

        async def post_json(self, url, body, **options):
            calls.append((url, body, options))
            if url.endswith("accessToken"):
                return {"accessToken": "token"}
            return {"downloadUrl": "https://media.dingtalk.com/object"}

    async def fetch(url, settings, domains):
        assert url == "https://media.dingtalk.com/object"
        return b"file"

    monkeypatch.setattr(media, "download", fetch)
    reader = DingTalkAttachments(
        Client(),
        DingTalkConnectionSettings(
            client_id="robot", client_secret_ref="MEDIA_TEST_SECRET"
        ),
        AttachmentSettings(),
    )
    assert await reader.read("download-code") == b"file"
    assert calls[0][1] == {"appKey": "robot", "appSecret": "secret"}
    assert calls[1][1] == {
        "downloadCode": "download-code",
        "robotCode": "robot",
    }
    assert calls[1][2] == {"headers": {"x-acs-dingtalk-access-token": "token"}}


@pytest.mark.anyio
async def test_cancelled_attachment_read_can_retry():
    import anyio

    files = AttachmentCollection()
    metadata = Attachment.create("file", "text/plain")
    attempts = []

    async def read():
        attempts.append(1)
        if len(attempts) == 1:
            await anyio.sleep_forever()
        return b"done"

    files.add(metadata, read)
    with anyio.move_on_after(0.01):
        await files.read(metadata.id)
    assert await files.read(metadata.id) == b"done"
