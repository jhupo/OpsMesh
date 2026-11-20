"""Exercise the standalone QQ adapter without connecting to QQ."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from opsmesh import on_fullmatch
from opsmesh.config import Config, Env
from opsmesh.drivers import Request, Response, combine_driver
from opsmesh.drivers.aiohttp import Mixin
from opsmesh.drivers.fastapi import Driver
from qq import Adapter, C2CMessageCreateEvent, Gateway
from qq import gateway as gateway_module
from qq.adapter import _Session
from qq.config import Account, Settings
from qq.exception import ApiNotAvailable

SECRET = "mock-qq-secret"


def test_installed_plugin_loads_with_namespaced_configuration():
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import sys
sys.path[:0] = sys.argv[1:]
import opsmesh
opsmesh.init(driver='~fastapi+~aiohttp', plugin_settings={
    'other': {'enabled': True},
    'qq': {'accounts': [{'app_id': '123', 'secret_ref': 'QQ_TEST_SECRET', 'transport': 'webhook'}]},
})
plugin = opsmesh.load_plugin('qq')
assert plugin and plugin.metadata.type == 'adapter'
assert not opsmesh.get_adapters()
plugin.manager.enable_plugin(plugin)
opsmesh.get_driver().run = lambda: None
opsmesh.run()
assert opsmesh.get_adapter('qq')._accounts['123'].id == 'qq:123'
""",
            str(Path(sys.modules["opsmesh"].__file__).resolve().parents[1]),
            str(Path(sys.modules["qq"].__file__).resolve().parents[1]),
        ],
        check=True,
        env={**os.environ, "QQ_TEST_SECRET": SECRET},
    )


def create_adapter(monkeypatch, transport):
    monkeypatch.setenv("QQ_TEST_SECRET", SECRET)
    account = Account(
        app_id="123", secret_ref="QQ_TEST_SECRET", transport=transport
    )
    driver = combine_driver(Driver, Mixin)(Env(), Config())
    return Adapter(driver, settings=Settings(accounts=[account]))


def message(sequence=None):
    return {
        "op": 0,
        "t": "C2C_MESSAGE_CREATE",
        "s": sequence,
        "id": "evt-1",
        "d": {
            "id": "msg-1",
            "content": "hello",
            "timestamp": "2026-11-19T00:00:00Z",
            "author": {"id": "author", "user_openid": "openid"},
        },
    }


def test_webhook_verifies_then_dispatches_and_cleans_up(monkeypatch):
    adapter = create_adapter(monkeypatch, "webhook")
    key = Ed25519PrivateKey.from_private_bytes((SECRET.encode() * 32)[:32])

    async def run():
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def handle(gateway, event):
            assert isinstance(gateway, Gateway)
            assert isinstance(event, C2CMessageCreateEvent)
            assert event.get_plaintext() == "hello"
            assert event.is_tome()
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        monkeypatch.setattr(gateway_module, "handle_event", handle)
        await adapter.startup()
        body = json.dumps(message()).encode()
        headers = {"X-Bot-Appid": "123", "X-Signature-Timestamp": "123"}

        async def post(raw):
            return await adapter._handle_http(
                Request("POST", "/qq/webhook", headers=headers, content=raw)
            )

        challenge = await post(
            b'{"op":13,"d":{"event_ts":"123","plain_token":"token"}}'
        )
        assert challenge.status_code == 200
        key.public_key().verify(
            bytes.fromhex(json.loads(challenge.content)["signature"]),
            b"123token",
        )
        assert not adapter.gateways
        headers["X-Signature-Ed25519"] = "z" * 128
        assert (await post(body)).status_code == 403
        assert not adapter.driver.gateways
        headers["X-Signature-Ed25519"] = key.sign(b"123" + body).hex()
        assert (await post(body + b" ")).status_code == 403
        assert (await post(body)).status_code == 200
        await asyncio.wait_for(started.wait(), 1)
        assert adapter.driver.gateways["qq:123"] is adapter.gateways["qq:123"]
        await adapter.shutdown()
        assert stopped.is_set()
        assert not adapter.driver.gateways
        assert (await post(body)).status_code == 503

    asyncio.run(run())


class Socket:
    def __init__(self, *frames):
        self.frames = iter(frames)
        self.sent = []

    async def receive(self):
        frame = next(self.frames, None)
        if frame is None:
            await asyncio.Future()
        return json.dumps(frame)

    async def send(self, data):
        self.sent.append(json.loads(data))


def test_shards_share_registration_and_resume(monkeypatch):
    adapter = create_adapter(monkeypatch, "websocket")
    hello = {"op": 10, "d": {"heartbeat_interval": 10000}}

    def ready(index):
        return {
            "op": 0,
            "t": "READY",
            "s": 1,
            "d": {
                "version": 1,
                "session_id": f"session-{index}",
                "user": {"id": "123", "username": "bot"},
                "shard": [index, 2],
            },
        }

    async def run():
        gateway = adapter._accounts["123"]
        seen = asyncio.Queue()

        async def consume(event):
            await seen.put(event)

        async def token():
            return "QQBot token"

        monkeypatch.setattr(gateway, "handle_event", consume)
        monkeypatch.setattr(gateway, "_get_authorization_header", token)
        adapter._running = True
        sessions = [_Session(), _Session()]
        sockets = [Socket(hello, ready(i)) for i in range(2)]
        tasks = [
            asyncio.create_task(
                adapter._run_socket(gateway, (i, 2), sessions[i], sockets[i])
            )
            for i in range(2)
        ]
        try:
            for _ in tasks:
                await asyncio.wait_for(seen.get(), 1)
            assert len(adapter.gateways) == 1
            assert [ws.sent[0]["d"]["shard"] for ws in sockets] == [
                [0, 2],
                [1, 2],
            ]
            tasks[0].cancel()
            await asyncio.gather(tasks[0], return_exceptions=True)
            assert gateway.id in adapter.driver.gateways
            resumed = Socket(
                hello,
                message(2),
                {"op": 0, "t": "RESUMED", "s": 3, "d": {}},
                {"op": 7},
            )
            await adapter._run_socket(gateway, (0, 2), sessions[0], resumed)
            assert resumed.sent[0]["op"] == 6
            assert resumed.sent[0]["d"]["seq"] == 1
            assert isinstance(
                await asyncio.wait_for(seen.get(), 1), C2CMessageCreateEvent
            )
            assert sessions[0].sequence == 3
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await adapter.shutdown()
        assert not adapter.driver.gateways

    asyncio.run(run())


def test_reply_and_401_token_refresh(monkeypatch):
    adapter = create_adapter(monkeypatch, "webhook")

    async def run():
        tokens = iter(("old-token", "new-token"))
        auth = []

        async def request(request):
            assert request.url.host == "api.bot.qq.com"
            if request.url.path == "/app/getAppAccessToken":
                assert request.json == {"appId": "123", "clientSecret": SECRET}
                return Response(
                    200,
                    content=json.dumps(
                        {"access_token": next(tokens), "expires_in": 3600}
                    ),
                )
            assert request.url.path == "/v2/users/openid/messages"
            assert request.json["content"] == "world"
            assert request.json["msg_id"] == "msg-1"
            auth.append(request.headers["Authorization"])
            return (
                Response(401)
                if len(auth) == 1
                else Response(200, content='{"id":"sent"}')
            )

        monkeypatch.setattr(adapter, "request", request)
        await adapter.startup()
        gateway = adapter._accounts["123"]
        event = C2CMessageCreateEvent(event_id="evt-1", **message()["d"])
        try:
            assert (await gateway.send(event, "world")).id == "sent"
            assert auth == ["QQBot old-token", "QQBot new-token"]
            with pytest.raises(ApiNotAvailable):
                await gateway.call_api("clear_token")
        finally:
            await adapter.shutdown()
        assert gateway._access_token is None

    asyncio.run(run())


def test_signed_webhook_reaches_framework_matcher(monkeypatch):
    adapter = create_adapter(monkeypatch, "webhook")
    matcher = on_fullmatch("hello")

    async def run():
        seen = asyncio.Event()

        @matcher.handle()
        async def handle(gateway: Gateway, event: C2CMessageCreateEvent):
            assert gateway.id == "qq:123"
            assert event.get_user_id() == "openid"
            seen.set()

        await adapter.startup()
        body = json.dumps(message()).encode()
        key = Ed25519PrivateKey.from_private_bytes((SECRET.encode() * 32)[:32])
        response = await adapter._handle_http(
            Request(
                "POST",
                "/qq/webhook",
                content=body,
                headers={
                    "X-Bot-Appid": "123",
                    "X-Signature-Timestamp": "123",
                    "X-Signature-Ed25519": key.sign(b"123" + body).hex(),
                },
            )
        )
        try:
            assert response.status_code == 200
            await asyncio.wait_for(seen.wait(), 1)
        finally:
            await adapter.shutdown()

    try:
        asyncio.run(run())
    finally:
        matcher.destroy()


def test_presigned_upload_does_not_send_qq_credentials(monkeypatch):
    adapter = create_adapter(monkeypatch, "webhook")

    async def run():
        async def request(request):
            assert request.url.host == "upload.example.test"
            assert request.content == b"part"
            assert not request.headers
            return Response(204)

        monkeypatch.setattr(adapter, "request", request)
        await adapter.startup()
        try:
            gateway = adapter._accounts["123"]
            await gateway.put_upload_part(
                presigned_url="https://upload.example.test/part?signature=test",
                data=b"part",
            )
            with pytest.raises(ValueError, match="HTTPS"):
                await gateway.put_upload_part(
                    presigned_url="http://example.test", data=b"part"
                )
        finally:
            await adapter.shutdown()

    asyncio.run(run())
