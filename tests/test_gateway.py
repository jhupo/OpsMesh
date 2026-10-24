"""Native SDK integration and durable gateway regression cases. No model calls."""

import asyncio
import base64
import json
import os
import socket
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from cryptography.fernet import Fernet
from dingtalk_stream import CallbackMessage
from dingtalk_stream.frames import Headers
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    DirectMessageCreateEvent,
    GroupAtMessageCreateEvent,
    MessageCreateEvent,
    ReadyEvent,
)
from nonebot.adapters.qq.models import MessageMarkdown, SetMemberMuteState
from nonebot.adapters.qq.models import User as QQUser
from opsmesh_sdk.channels import ChannelAdapter, ChannelEvent, OperationRejected, OutgoingMessage
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.models import AgentInstance
from opsmesh.bootstrap.schema import load_models
from opsmesh.gateway.adapters.config import validate_config
from opsmesh.gateway.adapters.dingtalk.adapter import DingTalkChannelAdapter
from opsmesh.gateway.adapters.dingtalk.events import chatbot
from opsmesh.gateway.adapters.dingtalk.messages import session_url
from opsmesh.gateway.adapters.qq.adapter import QQChannelAdapter
from opsmesh.gateway.adapters.qq.api import call, native_apis
from opsmesh.gateway.adapters.qq.config import QQConfig
from opsmesh.gateway.adapters.qq.driver import build_driver
from opsmesh.gateway.adapters.qq.events import normalize
from opsmesh.gateway.delivery import enqueue as enqueue_reply
from opsmesh.gateway.models import (
    ChannelConfiguration,
    ChannelEventRecord,
    ChannelIdentity,
    ChannelOperation,
    InboundMessage,
    OutboundDelivery,
)
from opsmesh.gateway.operations import enqueue, execute_pending
from opsmesh.gateway.service import receive
from opsmesh.gateway.worker import GatewayWorker
from opsmesh.infra.database import Base, new_id, now
from opsmesh.infra.encryption import SecretCipher
from opsmesh.plugins.models import Plugin
from opsmesh.plugins.registry import PluginRegistry
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.tasks.models import Task
from opsmesh.users.models import User


def qq_payload():
    return {
        "id": "message",
        "content": "hello",
        "timestamp": "2026-10-24T01:00:00Z",
        "author": {"id": "actor", "user_openid": "person"},
    }


def ding_frame(kind="text", **changes):
    frame = CallbackMessage()
    frame.headers = Headers.from_dict({"messageId": "frame", "topic": "/v1.0/im/bot/messages/get"})
    frame.data = {
        "msgId": "message",
        "msgtype": kind,
        "senderStaffId": "person",
        "senderId": "opaque",
        "conversationId": "room",
        "conversationType": "2",
        "text": {"content": "hello"},
        "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=private",
        "sessionWebhookExpiredTime": int((now() + timedelta(hours=1)).timestamp() * 1000),
        **changes,
    }
    return frame


class QQIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_native_webhook_signature_verification_and_size_guard(self):
        received = []

        async def sink(event):
            received.append(event)

        driver, native = build_driver(
            QQConfig(app_id="app", secret="test-secret", transport="webhook"), sink, asyncio.Event()
        )
        async with driver.server_app.router.lifespan_context(driver.server_app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=driver.server_app), base_url="http://qq.test"
            ) as client:
                headers = {"X-Bot-Appid": "app"}
                verify = await client.post(
                    "/qq/webhook",
                    headers=headers,
                    json={"op": 13, "d": {"plain_token": "challenge", "event_ts": "123"}},
                )
                self.assertEqual(verify.status_code, 200)
                self.assertEqual(verify.json()["plain_token"], "challenge")
                invalid = await client.post(
                    "/qq/webhook",
                    headers={**headers, "X-Signature-Ed25519": "not-hex"},
                    json={"op": 0},
                )
                self.assertEqual(invalid.status_code, 403)
                too_large = await client.post(
                    "/qq/webhook", headers=headers, content=b"x" * (1024 * 1024 + 1)
                )
                self.assertEqual(too_large.status_code, 413)
                body = json.dumps(
                    {"op": 0, "s": 1, "t": "C2C_MESSAGE_CREATE", "id": "event", "d": qq_payload()}
                ).encode()
                bot = Bot(native, "app", native.qq_config.qq_bots[0])
                signature = native._get_ed25519_key(bot).sign(b"123" + body).hex()
                with patch.object(
                    Bot, "me", AsyncMock(return_value=QQUser(id="bot", username="bot"))
                ):
                    accepted = await client.post(
                        "/qq/webhook",
                        headers={
                            **headers,
                            "X-Signature-Timestamp": "123",
                            "X-Signature-Ed25519": signature,
                        },
                        content=body,
                    )
                    self.assertEqual(accepted.status_code, 200)
                    for _ in range(30):
                        if received:
                            break
                        await asyncio.sleep(0.01)
                self.assertEqual(received[0].text, "hello")

    async def test_conflicting_webhook_port_does_not_exit_worker(self):
        with socket.create_server(("127.0.0.1", 0)) as occupied:
            adapter = QQChannelAdapter(
                {
                    "app_id": "app",
                    "secret": "test",
                    "transport": "webhook",
                    "webhook_port": occupied.getsockname()[1],
                }
            )
            with self.assertRaises(OSError):
                await adapter.connect(AsyncMock())

    def test_conversations_and_native_attachments(self):
        payload = qq_payload()
        c2c = normalize(C2CMessageCreateEvent.model_validate(payload))
        self.assertEqual(
            (c2c.external_user_id, c2c.conversation_id, c2c.scope),
            ("person", "c2c:person", "private"),
        )
        group = normalize(
            GroupAtMessageCreateEvent.model_validate(
                {
                    **payload,
                    "group_id": "native",
                    "group_openid": "person",
                    "author": {
                        "id": "actor",
                        "member_openid": "member",
                        "member_role": "owner",
                        "bot": False,
                    },
                    "attachments": [
                        {"content_type": "image/png", "url": "https://example.com/image.png"}
                    ],
                }
            )
        )
        self.assertEqual(
            (group.conversation_id, group.scope, group.external_role),
            ("group:person", "group", "owner"),
        )
        self.assertTrue(any(part["type"] == "image" for part in group.content))
        guild_payload = {
            "id": "message",
            "guild_id": "guild",
            "channel_id": "room",
            "content": "hello",
            "author": {"id": "person"},
        }
        guild = normalize(MessageCreateEvent.model_validate(guild_payload))
        direct = normalize(DirectMessageCreateEvent.model_validate(guild_payload))
        self.assertEqual(guild.conversation_id, "guild:room")
        self.assertEqual(direct.conversation_id, "dms:guild:person")
        self.assertEqual(direct.reply_target["kind"], "dms")

    def test_ready_retains_no_resume_secret_and_is_not_a_task(self):
        event = normalize(
            ReadyEvent(version=1, session_id="resume-secret", user=QQUser(id="bot"), shard=(0, 1))
        )
        self.assertFalse(event.is_message)
        self.assertNotIn("resume-secret", json.dumps(event.data))

    async def test_native_event_preprocessor_without_global_driver(self):
        received = []

        async def sink(event):
            received.append(event)

        config = QQConfig(app_id="app", secret="test-secret")
        _, native = build_driver(config, sink, asyncio.Event())
        bot = Bot(native, "app", BotInfo(id="app", secret="test-secret"))
        bot.self_info = QQUser(id="bot", username="bot")
        await bot.handle_event(C2CMessageCreateEvent.model_validate(qq_payload()))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].text, "hello")

    async def test_native_api_models_and_group_management(self):
        bot = Mock()
        bot.call_api = AsyncMock(return_value=None)
        await call(
            bot,
            "set_group_members_mute",
            {"group_id": "g", "members": [{"op": "add", "member_openid": "u"}]},
        )
        self.assertIsInstance(bot.call_api.call_args.kwargs["members"][0], SetMemberMuteState)
        await call(
            bot,
            "post_c2c_messages",
            {"openid": "u", "msg_type": 2, "markdown": {"content": "hello"}},
        )
        self.assertIsInstance(bot.call_api.call_args.kwargs["markdown"], MessageMarkdown)
        for method in (
            "get_group_info",
            "get_group_members",
            "get_group_join_request_list",
            "approval_join_request",
        ):
            self.assertIn(method, native_apis())
        self.assertEqual(
            len(QQChannelAdapter({"app_id": "a", "secret": "s"}).capabilities["operations"]),
            len(native_apis()) + 1,
        )

    async def test_native_binary_upload_does_not_accept_local_paths(self):
        bot = Mock(call_api=AsyncMock(return_value=None))
        await call(
            bot,
            "post_messages",
            {"channel_id": "c", "file_image": {"$base64": base64.b64encode(b"image").decode()}},
        )
        self.assertEqual(bot.call_api.call_args.kwargs["file_image"], b"image")
        with self.assertRaises(OperationRejected):
            await call(bot, "post_messages", {"channel_id": "c", "file_image": "C:/private.png"})
        with self.assertRaises(OperationRejected):
            await call(
                bot,
                "put_upload_part",
                {"presigned_url": "http://127.0.0.1/private", "data": {"$base64": "eA=="}},
            )

    async def test_native_api_rejects_unknown_or_invalid_before_network(self):
        bot = Mock(call_api=AsyncMock())
        for name, parameters in (
            ("get_authorization_header", {}),
            ("get_group_info", {}),
            ("get_group_info", {"group_id": "g", "secret": "s"}),
        ):
            with self.assertRaises(OperationRejected):
                await call(bot, name, parameters)
        bot.call_api.assert_not_called()

    async def test_all_reply_targets_keep_native_message_id_and_sequence(self):
        adapter = QQChannelAdapter({"app_id": "app", "secret": "test"})
        bot = SimpleNamespace(
            **{
                name: AsyncMock(return_value={"id": "sent"})
                for name in ("send_to_c2c", "send_to_group", "send_to_channel", "send_to_dms")
            }
        )
        adapter.native = SimpleNamespace(bots={"app": bot})
        adapter.ready.set()
        for kind, method in (
            ("c2c", "send_to_c2c"),
            ("group", "send_to_group"),
            ("guild", "send_to_channel"),
            ("dms", "send_to_dms"),
        ):
            await adapter.send(
                OutgoingMessage(
                    "out",
                    "conversation",
                    "reply",
                    reply_to="normalized-id",
                    sequence=7,
                    target={"kind": kind, "id": "room", "message_id": "native-id"},
                )
            )
            arguments = getattr(bot, method).call_args
            self.assertEqual(arguments.kwargs["msg_id"], "native-id")
            if kind in {"c2c", "group"}:
                self.assertEqual(arguments.kwargs["msg_seq"], 7)

    def test_configuration_does_not_accept_old_secrets_or_unknown_intents(self):
        for config in (
            {"app_id": "a", "app_secret": "old"},
            {"app_id": "a", "secret": "s", "intents": {"imaginary": True}},
        ):
            with self.assertRaises(InvalidArgumentError):
                validate_config("qq", config)
        config = validate_config("qq", {"app_id": "a", "secret": "s"})
        self.assertEqual(config["secret"], "s")
        self.assertTrue(config["intents"]["c2c_group_at_messages"])


class DingTalkIntegration(unittest.IsolatedAsyncioTestCase):
    def test_text_rich_media_and_routing_secrets(self):
        text_event = chatbot(ding_frame(isAdmin=True))
        self.assertEqual(text_event.text, "hello")
        self.assertEqual(text_event.external_user_id, "person")
        self.assertEqual(text_event.external_role, "group_admin")
        self.assertNotIn("sessionWebhook", text_event.data)
        self.assertIn("webhook", text_event.reply_target)
        picture = chatbot(ding_frame("picture", content={"downloadCode": "download"}))
        self.assertEqual(picture.content[0]["data"]["download_code"], "download")
        rich = chatbot(
            ding_frame(
                "richText", content={"richText": [{"text": "rich"}, {"downloadCode": "image"}]}
            )
        )
        self.assertEqual(rich.text, "rich")
        self.assertEqual(len(rich.content), 2)
        for kind in ("file", "audio", "video"):
            event = chatbot(ding_frame(kind, content={"downloadCode": "blob", "fileName": "name"}))
            self.assertEqual(event.content[0]["type"], kind)
            self.assertEqual(event.content[0]["data"]["downloadCode"], "blob")

    def test_session_urls_expiration_and_origin(self):
        target = chatbot(ding_frame()).reply_target
        self.assertIn("sendBySession", session_url(target))
        for url in (
            "http://oapi.dingtalk.com/robot/sendBySession",
            "https://evil.example/robot/sendBySession",
            "https://oapi.dingtalk.com@evil.example/robot/sendBySession",
            "https://oapi.dingtalk.com:444/robot/sendBySession",
        ):
            with self.assertRaises(OperationRejected):
                session_url({**target, "webhook": url})
        with self.assertRaises(OperationRejected):
            session_url({**target, "expires_at": 1})

    async def adapter(self, handler):
        adapter = DingTalkChannelAdapter({"client_id": "id", "client_secret": "secret"})
        await adapter.http.aclose()
        adapter.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter.api.http = adapter.http
        adapter.client.get_access_token = Mock(return_value="private-token")
        self.addAsyncCleanup(adapter.close)
        return adapter

    async def test_rich_session_reply_and_strict_failure(self):
        requests = []

        def transport(request):
            requests.append(request)
            return httpx.Response(200, json={"errcode": 0})

        adapter = await self.adapter(transport)
        await adapter.send(
            OutgoingMessage(
                "id",
                "conversation",
                "",
                target=chatbot(ding_frame()).reply_target,
                content=[{"type": "markdown", "data": {"title": "T", "text": "M"}}],
            )
        )
        self.assertEqual(json.loads(requests[0].content)["msgtype"], "markdown")
        for data in ({}, {"errcode": 40014}):
            response = httpx.Response(200, json=data, request=requests[0])
            with self.assertRaises((OperationRejected, ValueError)):
                adapter.api.response(response, webhook=True)

    async def test_api_auth_fixed_hosts_proactive_cards_and_media(self):
        requests = []

        def transport(request):
            requests.append(request)
            return httpx.Response(200, json={"media_id": "uploaded", "outTrackId": "card"})

        adapter = await self.adapter(transport)
        await adapter.execute(
            "api.request",
            {
                "method": "POST",
                "path": "/v1.0/card/instances",
                "body": {"cardTemplateId": "template"},
            },
        )
        self.assertEqual(requests[-1].url.host, "api.dingtalk.com")
        self.assertEqual(requests[-1].headers["x-acs-dingtalk-access-token"], "private-token")
        await adapter.send(
            OutgoingMessage(
                "id", "conversation", "hello", target={"kind": "group", "conversation_id": "group"}
            )
        )
        self.assertEqual(requests[-1].url.path, "/v1.0/robot/groupMessages/send")
        await adapter.send(
            OutgoingMessage(
                "id", "conversation", "hello", target={"kind": "private", "user_ids": ["staff"]}
            )
        )
        self.assertEqual(requests[-1].url.path, "/v1.0/robot/oToMessages/batchSend")
        await adapter.execute(
            "media.upload", {"type": "image", "filename": "image.png", "content_base64": "aW1hZ2U="}
        )
        self.assertEqual(requests[-1].url.host, "oapi.dingtalk.com")
        self.assertIn(b"image", requests[-1].content)

    async def test_api_rejects_redirect_auth_and_path_overrides(self):
        transport = Mock(return_value=httpx.Response(200, json={}))
        adapter = await self.adapter(transport)
        for parameters in (
            {"path": "https://evil.example"},
            {"path": "//evil.example"},
            {"path": "/%2e%2e/private"},
            {"path": "/safe", "query": {"access_token": "override"}},
            {"path": "/safe", "headers": {"Authorization": "override"}},
        ):
            with self.assertRaises(OperationRejected):
                await adapter.execute("api.request", parameters)
        transport.assert_not_called()
        with self.assertRaises(OperationRejected):
            await adapter.execute(
                "media.upload", {"type": "file", "filename": "../private", "content_base64": "eA=="}
            )

    async def test_all_native_callback_topics_and_card_ack_after_persistence(self):
        received = []

        async def sink(event):
            received.append(event)

        adapter = await self.adapter(lambda request: httpx.Response(200, json={}))
        from opsmesh.gateway.adapters.dingtalk.handlers import register

        register(adapter.client, adapter.config, sink, adapter.transport_logger)
        self.assertIn("/v1.0/im/bot/messages/delegate", adapter.client.callback_handler_map)
        self.assertIn("/v1.0/graph/api/invoke", adapter.client.callback_handler_map)
        frame = ding_frame()
        frame.data = {
            "userId": "u",
            "outTrackId": "card",
            "content": json.dumps({"action": "yes"}),
            "extension": "{}",
        }
        await adapter.client.callback_handler_map["/v1.0/card/instances/callback"].process(frame)
        self.assertFalse(received[0].is_message)
        self.assertEqual(received[0].external_user_id, "u")
        failing = AsyncMock(side_effect=RuntimeError("database unavailable"))
        register(adapter.client, adapter.config, failing, adapter.transport_logger)
        with self.assertRaises(RuntimeError):
            await adapter.client.callback_handler_map["/v1.0/card/instances/callback"].process(
                frame
            )

    async def test_readiness_tracks_native_connection_and_cancellation(self):
        adapter = await self.adapter(lambda request: httpx.Response(200, json={}))
        connected = asyncio.Event()

        async def start():
            adapter.client.websocket = object()
            connected.set()
            await asyncio.Event().wait()

        adapter.client.start = start
        adapter.client.stop = AsyncMock()
        task = asyncio.create_task(adapter.connect(AsyncMock()))
        await connected.wait()
        await asyncio.wait_for(adapter.ready.wait(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(adapter.ready.is_set())
        adapter.client.stop.assert_awaited()


class MemoryChannel(ChannelAdapter):
    def __init__(self, action=None):
        super().__init__()
        self.action = action or AsyncMock(return_value={"ok": True})
        self.ready.set()

    @property
    def capabilities(self):
        return {"operations": {"native": {}}}

    async def connect(self, sink):
        await asyncio.Event().wait()

    async def send(self, message):
        return {"id": "receipt"}

    async def execute(self, operation, parameters):
        return await self.action(operation, parameters)

    async def close(self):
        self.ready.clear()


@unittest.skipUnless(
    os.environ.get("OPSMESH_TEST_DATABASE_URL"),
    "Requires an explicitly configured PostgreSQL test database",
)
class GatewayPersistence(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine(os.environ["OPSMESH_TEST_DATABASE_URL"])
        self.schema = "opsmesh_gateway_" + new_id().replace("-", "")
        with self.engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        load_models()
        bound = self.engine.execution_options(schema_translate_map={None: self.schema})
        Base.metadata.create_all(bound)
        self.sessions = sessionmaker(bound, expire_on_commit=False)
        self.cipher = SecretCipher(Fernet.generate_key().decode())
        self.folder = tempfile.TemporaryDirectory()
        with self.sessions.begin() as db:
            self.user = User(name="gateway-admin", roles=["admin"])
            db.add(self.user)
            db.flush()
            plugin = Plugin(
                owner_id=self.user.id, name="test", version="1", digest="0" * 64, manifest={}
            )
            db.add(plugin)
            db.flush()
            agent = AgentInstance(
                owner_id=self.user.id,
                name="test",
                plugin_id=plugin.id,
                entry="main",
                encrypted_config=self.cipher.encrypt({}),
            )
            db.add(agent)
            db.flush()
            self.channel = ChannelConfiguration(
                owner_id=self.user.id,
                name="test",
                agent_id=agent.id,
                adapter="qq",
                encrypted_config=self.cipher.encrypt({}),
                worker_id="worker",
                lease_until=now() + timedelta(minutes=2),
            )
            db.add(self.channel)
            db.flush()
            db.add(
                ChannelIdentity(
                    channel_id=self.channel.id, external_user_id="person", user_id=self.user.id
                )
            )

    def tearDown(self):
        with self.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.engine.dispose()
        self.folder.cleanup()

    def message(self, message_id="message", conversation="group:room", scope="group"):
        return ChannelEvent(
            message_id,
            "message.text",
            "person",
            conversation,
            "hello",
            True,
            scope,
            reply_target={"kind": "group", "id": "room", "message_id": "native"},
        )

    async def wait_operation(self, operation_id):
        for _ in range(100):
            with self.sessions() as db:
                row = db.get(ChannelOperation, operation_id)
                if row.status in {"succeeded", "failed", "uncertain"}:
                    return row
            await asyncio.sleep(0.01)
        self.fail("Operation did not finish")

    async def run_operations(self, adapter):
        task = asyncio.create_task(
            execute_pending(self.sessions, self.cipher, self.channel.id, adapter, "worker")
        )
        self.addAsyncCleanup(self.stop_task, task)
        return task

    @staticmethod
    async def stop_task(task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def test_event_deduplication_native_metadata_and_reply_sequence(self):
        with self.sessions.begin() as db:
            first = receive(db, self.cipher, self.channel.id, self.message())
            second = receive(db, self.cipher, self.channel.id, self.message())
            self.assertEqual(first, second)
            task = db.get(Task, first)
            context = self.cipher.decrypt(task.encrypted_input)["metadata"]["channel_event"]
            self.assertNotIn("reply_target", context)
            self.assertEqual(db.scalar(select(func.count()).select_from(Task)), 1)
            enqueue_reply(db, self.cipher, task, "first", "one")
            enqueue_reply(db, self.cipher, task, "second", "two")
            enqueue_reply(db, self.cipher, task, "first", "one")
            self.assertEqual(db.scalar(select(InboundMessage.reply_sequence)), 2)
            self.assertEqual(db.scalar(select(func.count()).select_from(OutboundDelivery)), 2)
            with self.assertRaises(ConflictError):
                enqueue_reply(db, self.cipher, task, "first", "changed")

    def test_nonmessage_and_unbound_events_persist_without_privilege(self):
        with self.sessions.begin() as db:
            receive(
                db,
                self.cipher,
                self.channel.id,
                ChannelEvent("notice", "GROUP_JOIN_REQUEST", data={"role": "owner"}),
            )
            receive(
                db,
                self.cipher,
                self.channel.id,
                replace(self.message(), external_user_id="unbound"),
            )
            self.assertEqual(db.scalar(select(func.count()).select_from(Task)), 0)
            self.assertEqual(
                set(db.scalars(select(ChannelEventRecord.status))), {"recorded", "unbound"}
            )

    def test_disabled_user_rejection_remains_durable(self):
        with self.sessions.begin() as db:
            db.get(User, self.user.id).enabled = False
            receive(db, self.cipher, self.channel.id, self.message())
            row = db.scalar(select(ChannelEventRecord))
            self.assertEqual((row.status, row.error_code), ("rejected", "forbidden"))
            self.assertEqual(db.scalar(select(func.count()).select_from(Task)), 0)

    def test_private_group_and_guild_sessions_are_isolated(self):
        with self.sessions.begin() as db:
            values = [
                self.message("a", "c2c:room", "private"),
                self.message("b"),
                self.message("c", "guild:room"),
                self.message("d", "dms:room:person", "private"),
            ]
            tasks = [
                db.get(Task, receive(db, self.cipher, self.channel.id, value)) for value in values
            ]
            self.assertEqual(len({task.session_id for task in tasks}), 4)

    async def test_native_operation_is_idempotent_and_encrypted(self):
        with self.sessions.begin() as db:
            row = enqueue(
                db,
                self.cipher,
                self.channel.id,
                self.user.id,
                "native",
                {"secret": "not-plaintext"},
                "key",
            )
            same = enqueue(
                db,
                self.cipher,
                self.channel.id,
                self.user.id,
                "native",
                {"secret": "not-plaintext"},
                "key",
            )
            self.assertEqual(row.id, same.id)
            self.assertNotIn("not-plaintext", row.encrypted_parameters)
            with self.assertRaises(ConflictError):
                enqueue(db, self.cipher, self.channel.id, self.user.id, "native", {}, "key")
        adapter = MemoryChannel()
        await self.run_operations(adapter)
        completed = await self.wait_operation(row.id)
        self.assertEqual(completed.status, "succeeded")
        self.assertEqual(self.cipher.decrypt(completed.encrypted_result), {"value": {"ok": True}})
        adapter.action.assert_awaited_once()

    async def test_native_operation_revalidates_admin_before_side_effect(self):
        with self.sessions.begin() as db:
            row = enqueue(db, self.cipher, self.channel.id, self.user.id, "native", {}, "key")
            db.get(User, self.user.id).roles = ["user"]
        adapter = MemoryChannel()
        await self.run_operations(adapter)
        completed = await self.wait_operation(row.id)
        self.assertEqual(
            (completed.status, completed.error_code), ("failed", "authorization_revoked")
        )
        adapter.action.assert_not_called()

    async def test_transport_loss_is_uncertain_and_not_replayed(self):
        with self.sessions.begin() as db:
            row = enqueue(db, self.cipher, self.channel.id, self.user.id, "native", {}, "key")
        adapter = MemoryChannel(AsyncMock(side_effect=TimeoutError()))
        await self.run_operations(adapter)
        completed = await self.wait_operation(row.id)
        self.assertEqual(completed.status, "uncertain")
        await asyncio.sleep(0.6)
        adapter.action.assert_awaited_once()

    async def test_restart_marks_claimed_operations_uncertain(self):
        with self.sessions.begin() as db:
            row = enqueue(db, self.cipher, self.channel.id, self.user.id, "native", {}, "key")
            row.status = "running"
            channel = db.get(ChannelConfiguration, self.channel.id)
            channel.worker_id, channel.lease_until = None, None
        worker = GatewayWorker(self.sessions, self.cipher, PluginRegistry(), Path(self.folder.name))
        with patch.object(worker, "adapter", return_value=MemoryChannel()):
            await worker.reconcile()
            await worker.stop(self.channel.id)
        with self.sessions() as db:
            row = db.get(ChannelOperation, row.id)
            self.assertEqual((row.status, row.error_code), ("uncertain", "operation_worker_lost"))


if __name__ == "__main__":
    unittest.main()
