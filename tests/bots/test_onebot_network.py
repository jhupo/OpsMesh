"""Temporary loopback OneBot peers exercise HTTP uploads and both WebSocket directions."""

import asyncio
import base64
import json
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from opsmesh.framework.adapters import OpsMeshMessage
from opsmesh.framework.drivers import OpsMeshNetworkDriver
from opsmesh.framework.drivers.asgi import mount_driver_routes
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.message import DispatchResult
from opsmesh.modules.bots.api import BotAccount

from . import test_onebot as onebot_helpers
from .helpers import create_bot_service as BotService
from .helpers import onebot_adapter
from .test_onebot import account, authorize, ignore, resolve_principal, secret, target, webhook

store = onebot_helpers.store


@asynccontextmanager
async def local_asgi(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="websockets-sansio"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        listener.close()


async def eventually(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("channel", ["private:200", "group:200"])
async def test_real_http_file_upload_dedup_and_unknown_outcome(store, channel):
    calls = []
    destination = target().model_copy(update={"channel_id": channel})

    async def upload(request):
        assert request.headers["authorization"] == "Bearer test-token"
        body = await request.json()
        identifier = "user_id" if channel.startswith("private:") else "group_id"
        assert body[identifier] == "200"
        if body["name"] == "unknown.txt":
            calls.append(body)
            return JSONResponse({}, status_code=503)
        assert body["name"] == "report.txt"
        assert base64.b64decode(body["file"].removeprefix("base64://")) == b"report-content"
        calls.append(body)
        return JSONResponse({"status": "ok", "retcode": 0, "data": {"file_id": "file-1"}})

    endpoint = "/upload_private_file" if channel.startswith("private:") else "/upload_group_file"
    app = Starlette(routes=[Route(endpoint, upload, methods=["POST"])])
    async with local_asgi(app) as url, httpx.AsyncClient() as client:
        service = BotService(store, secret, client, authorize, ignore)
        await service.create_account(
            account().model_copy(
                update={
                    "settings": {**account().settings, "api_base_url": url, "file_upload": "napcat"}
                }
            ),
            actor_id="admin",
        )
        receipt = await service.upload_file(
            destination, "report.txt", b"report-content", idempotency_key="file", actor_id="user"
        )
        assert receipt.file_id == "file-1"
        replacement = BotService(store, secret, client, authorize, ignore)
        assert (
            await replacement.upload_file(
                destination,
                "report.txt",
                b"report-content",
                idempotency_key="file",
                actor_id="user",
            )
        ).duplicate
        assert len(calls) == 1
        with pytest.raises(OpsMeshError) as conflict:
            await replacement.upload_file(
                destination, "report.txt", b"different", idempotency_key="file", actor_id="user"
            )
        assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
        for _ in range(2):
            with pytest.raises(OpsMeshError) as uncertain:
                await replacement.upload_file(
                    destination, "unknown.txt", b"x", idempotency_key="unknown", actor_id="user"
                )
            assert uncertain.value.code == "DELIVERY_UNKNOWN"
        assert len(calls) == 2  # An ambiguous upload is never automatically sent twice.
        with pytest.raises(OpsMeshError):
            await replacement.upload_file(
                target(), "../secret", b"x", idempotency_key="bad", actor_id="user"
            )


async def test_active_websocket_events_api_correlation_reconnect_and_duplicate(store):
    received = []
    connections = 0
    actions = []
    service = None

    async def dispatch(context):
        received.append(context.event.id)
        await service.send(
            target(), OpsMeshMessage("reply"), idempotency_key=context.event.id, actor_id="user"
        )
        return DispatchResult(context.event.id, matched=1)

    async def peer(connection):
        nonlocal connections
        connections += 1
        number = connections
        assert connection.request.headers["Authorization"] == "Bearer test-token"
        body, _ = webhook()
        await connection.send(body.decode())
        if number > 1:
            second, _ = webhook(message_id=2)
            await connection.send(second.decode())
        request = json.loads(await connection.recv())
        actions.append(request)
        await connection.send(
            json.dumps(
                {
                    "echo": request["echo"],
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": number},
                }
            )
        )
        if number == 1:
            async with asyncio.timeout(5):
                while not any(
                    record.data.get("kind") == "inbound" and record.data.get("state") == "processed"
                    for record in await store.list()
                ):
                    await asyncio.sleep(0.01)
            await connection.close()
        else:
            await connection.wait_closed()

    async with serve(peer, "127.0.0.1", 0) as server, httpx.AsyncClient() as client:
        port = server.sockets[0].getsockname()[1]
        driver = OpsMeshNetworkDriver()
        service = BotService(
            store,
            secret,
            client,
            authorize,
            dispatch,
            driver=driver,
            principal_resolver=resolve_principal,
        )
        await service.create_account(
            BotAccount(
                id="support",
                self_id="100",
                adapter="onebot_v11",
                settings={
                    "transport": "websocket",
                    "websocket_url": f"ws://127.0.0.1:{port}",
                    "reconnect_seconds": 0.05,
                },
                credentials={"access_token": "env:BOT_TOKEN"},
            ),
            actor_id="admin",
        )
        await service.bind_identity("support", "200", "user", actor_id="admin")
        async with driver.lifespan():
            await eventually(lambda: len(actions) == 2)
            assert len(received) == 2 and len(set(received)) == 2
            assert all(action["action"] == "send_msg" for action in actions)
            assert onebot_adapter(service).bots["support"].ref.bot_id == "support"
            await service.set_enabled("support", False, actor_id="admin")
            await eventually(lambda: not onebot_adapter(service).connections.sessions)
            assert not onebot_adapter(service).connections.tasks
            assert not onebot_adapter(service).bots


async def test_reverse_websocket_auth_api_and_reconnection(store):
    contexts = []

    async def dispatch(context):
        contexts.append(context)
        return DispatchResult(context.event.id, matched=1)

    driver = OpsMeshNetworkDriver()
    async with httpx.AsyncClient() as client:
        service = BotService(
            store,
            secret,
            client,
            authorize,
            dispatch,
            driver=driver,
            principal_resolver=resolve_principal,
        )
        await service.create_account(
            BotAccount(
                id="support",
                self_id="100",
                adapter="onebot_v11",
                settings={"transport": "reverse_websocket"},
                credentials={"access_token": "env:BOT_TOKEN"},
            ),
            actor_id="admin",
        )
        await service.bind_identity("support", "200", "user", actor_id="admin")

        @asynccontextmanager
        async def lifespan(app):
            async with driver.lifespan():
                mount_driver_routes(app, driver)
                yield

        async with local_asgi(Starlette(lifespan=lifespan)) as url:
            address = url.replace("http:", "ws:") + "/plugins/onebot/ws"
            headers = {
                "X-Self-ID": "100",
                "X-Client-Role": "Universal",
                "Authorization": "Bearer wrong",
            }
            with pytest.raises(InvalidStatus):
                async with connect(address, additional_headers=headers):
                    pytest.fail("Unauthenticated reverse connection accepted")
            headers["Authorization"] = "Bearer test-token"
            for sequence in (1, 2):
                async with connect(address, additional_headers=headers) as peer:
                    await eventually(
                        lambda: "support" in onebot_adapter(service).connections.sessions
                    )
                    body, _ = webhook(message_id=sequence)
                    await peer.send(body.decode())
                    await eventually(lambda expected=sequence: len(contexts) == expected)
                    operation = asyncio.create_task(
                        service.send(
                            target(),
                            OpsMeshMessage("hello"),
                            idempotency_key=f"out-{sequence}",
                            actor_id="user",
                        )
                    )
                    request = json.loads(await peer.recv())
                    await peer.send(
                        json.dumps(
                            {
                                "echo": request["echo"],
                                "status": "ok",
                                "retcode": 0,
                                "data": {"message_id": sequence},
                            }
                        )
                    )
                    assert (await operation).message_id == str(sequence)
                await eventually(lambda: not onebot_adapter(service).connections.sessions)
