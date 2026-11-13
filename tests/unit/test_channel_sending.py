"""Check native media protocols, preflight and partial-send boundaries."""

import base64
import json
from types import SimpleNamespace

import anyio
import httpx
import pytest

from opsmesh.plugins.channels import (
    Attachment,
    AttachmentCollection,
    AttachmentError,
    Message,
    MessageSegment,
    MessageSendError,
)
from opsmesh.plugins.channels.dingtalk.client import DingTalkClient
from opsmesh.plugins.channels.dingtalk.config import (
    DingTalkConnectionSettings,
    DingTalkSettings,
)
from opsmesh.plugins.channels.dingtalk.events import (
    parse_callback as ding_event,
)
from opsmesh.plugins.channels.dingtalk.sending import DingTalkSender
from opsmesh.plugins.channels.qq.channel import QQChannel
from opsmesh.plugins.channels.qq.client import QQClient
from opsmesh.plugins.channels.qq.config import QQConnectionSettings, QQSettings
from opsmesh.plugins.channels.qq.errors import QQSendError
from opsmesh.plugins.channels.qq.events import parse_callback as qq_event
from opsmesh.plugins.channels.qq.sending import QQSender
from opsmesh.plugins.channels.sending import prepare

PNG = b"\x89PNG\r\n\x1a\nimage"
MP4 = b"\x00\x00\x00\x18ftypmp42"


@pytest.mark.anyio
@pytest.mark.parametrize("previous,count", [(0, 6), (1, 5)])
async def test_qq_rejects_excess_replies_before_network_io(previous, count):
    event, channel, sequences = qq_context(True)
    for _ in range(previous):
        channel.next_sequence(event.message_id)
    message = Message([add_file(event)] * count)

    def unexpected(request):
        pytest.fail("Reply limit must be checked before uploading")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected)
    ) as http:
        sender = QQSender(QQClient(channel.protocol, http), channel, "secret")
        with pytest.raises(QQSendError, match="five replies"):
            await sender.send(event, message)
    assert len(sequences) == previous


@pytest.mark.anyio
async def test_qq_concurrent_replies_share_remaining_capacity():
    event, channel, sequences = qq_context(True)
    message = Message([add_file(event)] * 3)
    failures = []
    sent = []

    async def respond(request):
        await anyio.sleep(0)
        if request.url.path.endswith("getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token"})
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"file_info": "info"})
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": str(len(sent))})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        sender = QQSender(QQClient(channel.protocol, http), channel, "secret")

        async def send():
            try:
                await sender.send(event, message)
            except QQSendError as error:
                failures.append(error)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(send)
            tasks.start_soon(send)
    assert len(failures) == 1
    assert [part["msg_seq"] for part in sent] == [1, 2, 3]
    assert not channel._reply_locks


@pytest.mark.anyio
async def test_qq_cancelled_upload_releases_reply_capacity():
    event, channel, sequences = qq_context(True)
    segment = add_file(event)

    async def respond(request):
        if request.url.path.endswith("getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token"})
        await anyio.sleep_forever()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        with anyio.move_on_after(0.02) as scope:
            await QQSender(
                QQClient(channel.protocol, http), channel, "secret"
            ).send(event, segment)
    assert scope.cancel_called
    assert not sequences
    async with channel.replies(event.message_id, 5):
        pass


def add_file(event, name="image.png", mime="image/png", data=PNG, **options):
    attachment = event.attachments.add_bytes(
        Attachment.create(name, mime), data
    )
    return MessageSegment.attachment(attachment, **options)


def qq_context(group):
    event = qq_event(
        {
            "t": "GROUP_AT_MESSAGE_CREATE" if group else "C2C_MESSAGE_CREATE",
            "d": {
                "id": "incoming",
                "content": "hello",
                "group_openid": "group",
                "author": {"member_openid": "user", "user_openid": "user"},
            },
        },
        connection_id="bot",
    )
    sequences = []

    def sequence(message_id):
        sequences.append(message_id)
        return QQChannel.next_sequence(channel, message_id)

    channel = QQChannel(
        SimpleNamespace(),
        self_id="bot",
        owner_id="support",
        connection_name="bot",
        path="/qq",
        protocol=QQSettings(),
        settings=QQConnectionSettings(app_id="123", secret_ref="QQ_SECRET"),
    )
    channel.next_sequence = sequence
    return event, channel, sequences


def ding_context(group):
    event = ding_event(
        {
            "msgtype": "text",
            "text": {"content": "hello"},
            "msgId": "incoming",
            "conversationId": "group",
            "conversationType": "2" if group else "1",
            "senderStaffId": "staff",
            "isInAtList": True,
        },
        connection_id="bot",
    )
    channel = SimpleNamespace(
        settings=DingTalkConnectionSettings(
            client_id="robot",
            client_secret_ref="DING_MEDIA_SECRET",
        )
    )
    return event, channel


@pytest.mark.anyio
@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize(
    "kind,mime,data,name,file_type",
    [
        ("image", "image/png", PNG, "image.png", 1),
        ("file", "application/pdf", b"%PDF-1.7", "report.pdf", 4),
        ("audio", "audio/silk", b"#!SILK_V3voice", "voice.silk", 3),
        ("video", "video/mp4", MP4, "clip.mp4", 2),
    ],
)
async def test_qq_uploads_bytes_then_sends_native_media(
    group, kind, mime, data, name, file_type
):
    event, channel, sequences = qq_context(group)
    segment = add_file(event, name, mime, data)
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append((request.url, body))
        if request.url.path.endswith("getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token"})
        assert request.headers["Authorization"] == "QQBot token"
        if request.url.path.endswith("/files"):
            assert body["file_type"] == file_type
            assert base64.b64decode(body["file_data"]) == data
            assert body["file_name"] == name
            assert body["srv_send_msg"] is False
            return httpx.Response(200, json={"file_info": "native-reference"})
        assert body["media"] == {"file_info": "native-reference"}
        assert body["msg_type"] == 7
        assert body["content"] == " "
        return httpx.Response(200, json={"id": "sent"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        result = await QQSender(
            QQClient(channel.protocol, http), channel, "secret"
        ).send(event, segment)
    assert result == {"id": "sent"}
    expected = "/v2/groups/group" if group else "/v2/users/user"
    assert str(calls[1][0]).endswith(expected + "/files")
    assert str(calls[2][0]).endswith(expected + "/messages")
    assert sequences == ["incoming"]


@pytest.mark.anyio
@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize(
    "kind,mime,data,name,template",
    [
        ("image", "image/png", PNG, "image.png", "sampleImageMsg"),
        ("file", "application/pdf", b"%PDF-1.7", "report.pdf", "sampleFile"),
        ("audio", "audio/amr", b"#!AMR\nvoice", "voice.amr", "sampleAudio"),
        ("video", "video/mp4", MP4, "clip.mp4", "sampleVideo"),
    ],
)
async def test_dingtalk_uploads_and_uses_recipient_template(
    monkeypatch, group, kind, mime, data, name, template
):
    monkeypatch.setenv("DING_MEDIA_SECRET", "secret")
    event, channel = ding_context(group)
    cover = event.attachments.add_bytes(
        Attachment.create("cover.png", "image/png"), PNG
    )
    segment = add_file(
        event,
        name,
        mime,
        data,
        duration_ms=1200 if kind in {"audio", "video"} else None,
        thumbnail=cover if kind == "video" else None,
    )
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path.endswith("accessToken"):
            assert json.loads(request.content) == {
                "appKey": "robot",
                "appSecret": "secret",
            }
            return httpx.Response(200, json={"accessToken": "token"})
        if request.url.path == "/media/upload":
            assert request.url.params["access_token"] == "token"
            assert "multipart/form-data" in request.headers["Content-Type"]
            assert data in request.content or PNG in request.content
            return httpx.Response(200, json={"media_id": "@native-reference"})
        body = json.loads(request.content)
        assert body["msgKey"] == template
        assert body["robotCode"] == "robot"
        assert request.headers["x-acs-dingtalk-access-token"] == "token"
        params = json.loads(body["msgParam"])
        assert "@native-reference" in params.values()
        if kind in {"audio", "video"}:
            assert params["duration"] == "1200"
        if kind == "video":
            assert params["picMediaId"] == "@native-reference"
        if kind == "file":
            assert params["fileType"] == "pdf"
        if group:
            assert request.url.path.endswith("/groupMessages/send")
            assert body["openConversationId"] == "group"
        else:
            assert request.url.path.endswith("/oToMessages/batchSend")
            assert body["userIds"] == ["staff"]
        return httpx.Response(200, json={"processQueryKey": "sent"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        result = await DingTalkSender(
            DingTalkClient(DingTalkSettings(), http), channel
        ).send(event, segment)
    assert result["processQueryKey"] == "sent"
    assert len(calls) == (4 if kind == "video" else 3)


@pytest.mark.anyio
@pytest.mark.parametrize("protocol", ["qq", "dingtalk"])
async def test_partial_send_reports_receipts_without_replay(
    monkeypatch, protocol
):
    monkeypatch.setenv("DING_MEDIA_SECRET", "secret")
    event, channel, *_ = (
        qq_context(False) if protocol == "qq" else ding_context(False)
    )
    message = Message("before") + add_file(event) + Message("after")
    sends = []
    uploads = []

    def respond(request):
        if (
            "accessToken" in request.url.path
            or "getAppAccessToken" in request.url.path
        ):
            return httpx.Response(
                200, json={"accessToken": "token", "access_token": "token"}
            )
        if request.url.path.endswith(("/files", "/media/upload")):
            uploads.append(request)
            return httpx.Response(
                200, json={"file_info": "info", "media_id": "@media"}
            )
        assert len(uploads) == 1
        sends.append(json.loads(request.content))
        if len(sends) == 2:
            return httpx.Response(500)
        return httpx.Response(
            200, json={"id": "first", "processQueryKey": "first"}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        sender = (
            QQSender(QQClient(QQSettings(), http), channel, "secret")
            if protocol == "qq"
            else DingTalkSender(
                DingTalkClient(DingTalkSettings(), http), channel
            )
        )
        with pytest.raises(MessageSendError) as caught:
            await sender.send(event, message)
    assert caught.value.failed_index == 1
    assert len(caught.value.completed) == 1
    assert len(sends) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("protocol", ["qq", "dingtalk"])
async def test_invalid_later_media_prevents_even_initial_text(protocol):
    event, channel, *_ = (
        qq_context(False) if protocol == "qq" else ding_context(False)
    )
    message = Message("before") + add_file(event, data=b"not an image")
    calls = []

    def respond(request):
        calls.append(request)
        raise AssertionError("Invalid media must not start network I/O")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        sender = (
            QQSender(QQClient(QQSettings(), http), channel, "secret")
            if protocol == "qq"
            else DingTalkSender(
                DingTalkClient(DingTalkSettings(), http), channel
            )
        )
        with pytest.raises(AttachmentError):
            await sender.send(event, message)
    assert not calls


@pytest.mark.anyio
async def test_prepare_keeps_order_and_rejects_invalid_duration():
    files = AttachmentCollection()
    data = files.add_bytes(
        Attachment.create("audio.amr", "audio/amr"), b"#!AMR\n"
    )
    message = (
        Message("a")
        + Message("b")
        + MessageSegment.attachment(data, duration_ms=True)
    )
    with pytest.raises(AttachmentError, match="duration"):
        await prepare(message, files, 10)
    parts = await prepare(
        Message("a") + Message("b") + MessageSegment.attachment(data), files, 10
    )
    assert parts[0] == "ab"
    assert parts[1].attachment.id == data.id


@pytest.mark.anyio
@pytest.mark.parametrize("protocol", ["qq", "dingtalk"])
async def test_cancelled_upload_never_starts_send(monkeypatch, protocol):
    monkeypatch.setenv("DING_MEDIA_SECRET", "secret")
    event, channel, *_ = (
        qq_context(False) if protocol == "qq" else ding_context(False)
    )
    calls = []

    async def respond(request):
        calls.append(request.url.path)
        if (
            "accessToken" in request.url.path
            or "getAppAccessToken" in request.url.path
        ):
            return httpx.Response(
                200, json={"accessToken": "token", "access_token": "token"}
            )
        await anyio.sleep_forever()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        sender = (
            QQSender(QQClient(QQSettings(), http), channel, "secret")
            if protocol == "qq"
            else DingTalkSender(
                DingTalkClient(DingTalkSettings(), http), channel
            )
        )
        with anyio.move_on_after(0.05) as scope:
            await sender.send(event, Message("before") + add_file(event))
    assert scope.cancel_called
    assert len(calls) == 2
    assert calls[-1].endswith(("/files", "/media/upload"))


@pytest.mark.anyio
@pytest.mark.parametrize("protocol", ["qq", "dingtalk"])
@pytest.mark.parametrize("upload_failure", [False, True])
async def test_mixed_reply_uploads_all_files_before_ordered_sends(
    monkeypatch, protocol, upload_failure
):
    monkeypatch.setenv("DING_MEDIA_SECRET", "secret")
    event, channel, *_ = (
        qq_context(False) if protocol == "qq" else ding_context(False)
    )
    message = Message("first") + add_file(event) + Message("middle")
    message += add_file(event, "report.pdf", "application/pdf", b"%PDF-1.7")
    message += Message("last")
    calls = []
    uploaded = []

    def respond(request):
        if request.url.path.endswith(("accessToken", "getAppAccessToken")):
            return httpx.Response(
                200,
                json={
                    "accessToken": "token",
                    "access_token": "token",
                },
            )
        if request.url.path.endswith(("/files", "/media/upload")):
            uploaded.append(request)
            if upload_failure and len(uploaded) == 2:
                return httpx.Response(200, json={"code": 400, "errcode": 400})
            return httpx.Response(
                200,
                json={
                    "file_info": f"info-{len(uploaded)}",
                    "media_id": f"@media-{len(uploaded)}",
                },
            )
        assert len(uploaded) == 2
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={"id": str(len(calls)), "processQueryKey": str(len(calls))},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        sender = (
            QQSender(QQClient(QQSettings(), http), channel, "secret")
            if protocol == "qq"
            else DingTalkSender(
                DingTalkClient(DingTalkSettings(), http), channel
            )
        )
        if upload_failure:
            with pytest.raises(Exception, match="rejected"):
                await sender.send(event, message)
            assert calls == []
        else:
            result = await sender.send(event, message)
            assert len(result) == 5
            if protocol == "qq":
                assert [part["msg_type"] for part in calls] == [0, 7, 0, 7, 0]
                assert [part["msg_seq"] for part in calls] == [1, 2, 3, 4, 5]
                assert [calls[i]["content"] for i in [0, 2, 4]] == [
                    "first",
                    "middle",
                    "last",
                ]
            else:
                assert [part["msgKey"] for part in calls] == [
                    "sampleText",
                    "sampleImageMsg",
                    "sampleText",
                    "sampleFile",
                    "sampleText",
                ]


@pytest.mark.anyio
async def test_dingtalk_requires_private_staff_id_before_upload():
    from opsmesh.plugins.channels.dingtalk.errors import DingTalkSendError

    event, channel = ding_context(False)
    event.raw.pop("senderStaffId")
    with pytest.raises(DingTalkSendError, match="staff user ID"):
        await DingTalkSender(
            DingTalkClient(DingTalkSettings(), None), channel
        ).send(event, add_file(event))


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["audio", "video"])
async def test_dingtalk_does_not_invent_duration_or_cover(kind):
    event, channel = ding_context(False)
    segment = add_file(
        event,
        "clip.mp4" if kind == "video" else "voice.amr",
        "video/mp4" if kind == "video" else "audio/amr",
        MP4 if kind == "video" else b"#!AMR\nvoice",
    )
    with pytest.raises(AttachmentError, match="duration_ms"):
        await DingTalkSender(
            DingTalkClient(DingTalkSettings(), None), channel
        ).send(event, segment)


@pytest.mark.anyio
async def test_vendor_image_guess_is_corrected_before_upload():
    event, _ = ding_context(False)
    part = add_file(event, "attachment", "image/jpeg", PNG)
    prepared = await prepare(part, event.attachments, 100)
    assert prepared[0].attachment.media_type == "image/png"


@pytest.mark.anyio
async def test_upload_rejects_redirect_without_exposing_token():
    from opsmesh.plugins.channels.dingtalk.errors import DingTalkSendError

    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://other.test"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(redirect), follow_redirects=True
    ) as http:
        client = DingTalkClient(DingTalkSettings(), http)
        with pytest.raises(DingTalkSendError) as caught:
            await client.upload_media(
                "secret-token",
                Attachment.create("a.png", "image/png"),
                PNG,
                kind="image",
            )
    assert len(requests) == 1
    assert "secret-token" not in str(caught.value)
