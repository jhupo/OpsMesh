"""QQ media upload/send sequencing against a controlled official-API peer."""

import json

import httpx
import pytest

from opsmesh.framework.adapters import (
    BotConnectionConfiguration,
    BotRef,
    OpsMeshMessage,
    OpsMeshMessageSegment,
    ReplyTarget,
)
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.qq import QQAdapter
from opsmesh.infrastructure.bots.qq.media import media_upload
from opsmesh.infrastructure.bots.qq.message import decode_message


@pytest.mark.parametrize("kind,path", [("group", "groups"), ("c2c", "users")])
@pytest.mark.parametrize("media_type,file_type", [("image", 1), ("video", 2), ("audio", 3)])
async def test_rich_media_upload_never_sends_implicitly_and_reply_preserves_sequence(
    kind, path, media_type, file_type
):
    requests = []

    def peer(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path.endswith("getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "synthetic", "expires_in": 7200})
        if request.url.path.endswith("/files"):
            assert request.url.path == f"/v2/{path}/target/files"
            assert body == {
                "file_type": file_type,
                "url": "https://cdn.example.test/resource",
                "srv_send_msg": False,
            }
            return httpx.Response(200, json={"file_info": "opaque-upload-receipt"})
        assert request.url.path == f"/v2/{path}/target/messages"
        assert body == {
            "msg_type": 7,
            "content": "caption",
            "media": {"file_info": "opaque-upload-receipt"},
            "msg_id": "original",
            "msg_seq": 3,
        }
        return httpx.Response(200, json={"id": "confirmed"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(peer)) as client:
        adapter = QQAdapter(OpsMeshDriver(), client, lambda ref: "synthetic-secret")
        bot = adapter.get_bot(
            BotConnectionConfiguration(
                id="bot",
                adapter="qq",
                self_id="100",
                settings={"app_id": "100"},
                credentials={"client_secret": "env:QQ_CLIENT_SECRET"},
            )
        )
        target = ReplyTarget(
            bot=BotRef(adapter_id="qq", bot_id="bot"),
            channel_id=f"{kind}:target",
            message_id="original",
            delivery_sequence=3,
        )
        message = OpsMeshMessage("caption") + OpsMeshMessageSegment(
            type=media_type, data={"resource_ref": "https://cdn.example.test/resource"}
        )
        assert await bot.send(target, message) == "confirmed"
    assert [path.rsplit("/", 1)[-1] for path, body in requests] == [
        "getAppAccessToken",
        "files",
        "messages",
    ]


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "http://example.test/a", "https://user:secret@example.test/a"]
)
def test_invalid_media_sources_fail_without_network(url):
    with pytest.raises(OpsMeshError):
        media_upload(OpsMeshMessage(OpsMeshMessageSegment.image(url)))


def test_incoming_media_preserves_type_and_does_not_download_content():
    message = decode_message(
        {
            "attachments": [
                {"url": "//cdn.example.test/image", "content_type": "image/png"},
                {"url": "https://cdn.example.test/audio", "content_type": "audio/silk"},
                {"url": "https://cdn.example.test/video", "content_type": "video/mp4"},
            ]
        },
        self_id="100",
    )
    assert [segment.type for segment in message] == ["image", "audio", "video"]
