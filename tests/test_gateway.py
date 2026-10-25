"""Native SDK integration and durable gateway regression cases. No model calls."""

import asyncio
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from cryptography.fernet import Fernet
from dingtalk_stream import CallbackMessage
from dingtalk_stream.frames import Headers
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.models import AgentInstance
from opsmesh.bootstrap.schema import load_models
from opsmesh.gateway.adapters.base import Adapter
from opsmesh.gateway.adapters.dingtalk.adapter import DingTalkAdapter
from opsmesh.gateway.adapters.dingtalk.events import chatbot
from opsmesh.gateway.adapters.dingtalk.messages import session_url
from opsmesh.gateway.adapters.errors import OperationRejected
from opsmesh.gateway.adapters.schemas import (
    Capabilities,
    Event,
    Message,
    Operation,
    Receipt,
    Result,
)
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
from opsmesh.shared.errors import ConflictError
from opsmesh.tasks.models import Task
from opsmesh.users.models import User


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


class DingTalkIntegration(unittest.IsolatedAsyncioTestCase):
    def test_text_rich_media_and_routing_secrets(self):
        text_event = chatbot(ding_frame(isAdmin=True))
        self.assertEqual(text_event.text, "hello")
        self.assertEqual(text_event.external_user_id, "person")
        self.assertIsNone(text_event.external_role)
        self.assertNotIn("sessionWebhook", text_event.data)
        self.assertNotIn("sessionWebhook", text_event.data["payload"])
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
            self.assertEqual(event.content[0]["data"]["download_code"], "blob")

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
        adapter = DingTalkAdapter({"client_id": "id", "client_secret": "secret"})
        adapter.set_state("connected")
        await adapter.http.aclose()
        adapter.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter.api.http = adapter.http
        adapter.client.get_access_token = Mock(return_value="private-token")
        self.addAsyncCleanup(adapter.http.aclose)
        return adapter

    async def test_rich_session_reply_and_strict_failure(self):
        requests = []

        def transport(request):
            requests.append(request)
            return httpx.Response(200, json={"errcode": 0})

        adapter = await self.adapter(transport)
        await adapter.send(
            Message(
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
            return httpx.Response(
                200, json={"media_id": "uploaded", "success": True, "processQueryKey": "receipt"}
            )

        adapter = await self.adapter(transport)
        await adapter.call(
            "dingtalk.card.create",
            {"cardTemplateId": "template", "outTrackId": "card", "cardData": {"cardParamMap": {}}},
        )
        self.assertEqual(requests[-1].url.host, "api.dingtalk.com")
        self.assertEqual(requests[-1].headers["x-acs-dingtalk-access-token"], "private-token")
        await adapter.send(
            Message(
                "id", "conversation", "hello", target={"kind": "group", "conversation_id": "group"}
            )
        )
        self.assertEqual(requests[-1].url.path, "/v1.0/robot/groupMessages/send")
        await adapter.send(
            Message(
                "id", "conversation", "hello", target={"kind": "private", "user_ids": ["staff"]}
            )
        )
        self.assertEqual(requests[-1].url.path, "/v1.0/robot/oToMessages/batchSend")
        await adapter.call(
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
                await adapter.call(
                    "dingtalk.card.create",
                    {
                        "cardTemplateId": "template",
                        "outTrackId": "card",
                        "cardData": {"cardParamMap": {}},
                        **parameters,
                    },
                )
        with self.assertRaises(OperationRejected):
            await adapter.call("api.request", {})
        transport.assert_not_called()
        with self.assertRaises(OperationRejected):
            await adapter.call(
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
        adapter.set_state("stopped")
        task = asyncio.create_task(adapter.run(AsyncMock()))
        await connected.wait()
        await asyncio.wait_for(adapter.ready.wait(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(adapter.ready.is_set())
        adapter.client.stop.assert_awaited()


class MemoryChannel(Adapter):
    def __init__(self, action=None):
        super().__init__()
        self.action = action or AsyncMock(return_value={"ok": True})
        self.ready.set()

    @property
    def capabilities(self):
        return Capabilities(
            implementation="memory",
            version="1",
            conversations=["private", "group"],
            events=["message"],
            message_parts=["text"],
            operations={
                "native": Operation(parameters={"type": "object"}, result={"type": "object"})
            },
        )

    async def run(self, sink):
        self.sink = sink
        self.set_state("connected")
        try:
            await asyncio.Event().wait()
        finally:
            self.sink = None
            self.set_state("stopped")

    async def send(self, message):
        return Receipt(value={"id": "receipt"})

    async def call(self, operation, parameters):
        return Result(operation=operation, value=await self.action(operation, parameters))


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
                capabilities=MemoryChannel().capabilities.model_dump(mode="json"),
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
        return Event(
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
                Event("notice", "GROUP_JOIN_REQUEST", data={"role": "owner"}),
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
        self.assertEqual(
            self.cipher.decrypt(completed.encrypted_result),
            {"value": {"operation": "native", "value": {"ok": True}}},
        )
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
