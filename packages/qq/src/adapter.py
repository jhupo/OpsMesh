"""QQ transport and gateway registration over existing OpsMesh drivers."""

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from opsmesh.adapters import Adapter as BaseAdapter
from opsmesh.adapters import Gateway as BaseGateway
from opsmesh.compat import PYDANTIC_V2, type_validate_json, type_validate_python
from opsmesh.drivers import (
    URL,
    ASGIMixin,
    Driver,
    HTTPClientMixin,
    HTTPServerSetup,
    Request,
    Response,
    Timeout,
    WebSocket,
    WebSocketClientMixin,
)

from .config import Account, Config, Settings
from .event import EVENT_CLASSES, Event, MessageAuditEvent, ReadyEvent
from .exception import ApiNotAvailable
from .gateway import Gateway
from .models import (
    Dispatch,
    Heartbeat,
    HeartbeatAck,
    Hello,
    Identify,
    InvalidSession,
    Payload,
    PayloadType,
    Reconnect,
    Resume,
    WebhookVerify,
)
from .utils import API, log

API_BASE = URL("https://api.bot.qq.com/")
HANDSHAKE_TIMEOUT = 30.0
MAX_WEBHOOK_BYTES = 1024 * 1024
MAX_PENDING_EVENTS = 128


@dataclass
class _Session:
    id: str | None = None
    sequence: int | None = None
    awaiting_ack: bool = False


class Adapter(BaseAdapter):
    @classmethod
    def prepare_settings(
        cls, driver: Driver, config: Config, bindings: dict[str, Account]
    ) -> dict[str, Any]:
        accounts = list(config.qq.accounts)
        cls._validate_driver(driver, accounts)
        for account in accounts:
            account.read_secret()
        ids = {account.app_id for account in accounts}
        for owner, account in bindings.items():
            path = f"{owner}.dependencies.qq"
            if account.app_id in ids:
                raise ValueError(f"{path}: duplicate QQ app_id {account.app_id}")
            try:
                cls._validate_driver(driver, [account])
                account.read_secret()
                if account.shard is not None and account.transport != "websocket":
                    raise ValueError("QQ shard is only valid for WebSocket accounts")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}: {exc}") from None
            ids.add(account.app_id)
            accounts.append(account)
        return {"settings": Settings(accounts=accounts), "bindings": bindings}

    @staticmethod
    def _validate_driver(driver: Driver, accounts: list[Account]) -> None:
        websocket = any(a.transport == "websocket" for a in accounts)
        webhook = any(a.transport == "webhook" for a in accounts)
        if accounts and not isinstance(driver, HTTPClientMixin):
            raise TypeError("QQ requires an HTTP client driver")
        if websocket and not isinstance(driver, WebSocketClientMixin):
            raise TypeError("QQ WebSocket requires a WebSocket client driver")
        if webhook and not isinstance(driver, ASGIMixin):
            raise TypeError("QQ Webhook requires an ASGI driver")

    def __init__(
        self,
        driver: Driver,
        *,
        settings: Settings,
        bindings: dict[str, Account] | None = None,
    ):
        super().__init__(driver)
        self._validate_driver(driver, settings.accounts)
        owners = {account.app_id: owner for owner, account in (bindings or {}).items()}
        self._accounts = {
            a.app_id: Gateway(self, a, a.read_secret(), owner_plugin_id=owners.get(a.app_id))
            for a in settings.accounts
        }
        self.connections = {
            key: self._accounts[account.app_id] for key, account in (bindings or {}).items()
        }
        self._webhooks = {
            key: gateway
            for key, gateway in self._accounts.items()
            if gateway.account.transport == "webhook"
        }
        self._workers: set[asyncio.Task[None]] = set()
        self._events: set[asyncio.Task[None]] = set()
        self._online_shards: dict[str, set[int]] = {}
        self._running = False
        self._route_registered = False
        self.on_ready(self.startup)
        self.driver.on_shutdown(self.shutdown)

    @classmethod
    def get_name(cls) -> str:
        return "qq"

    @staticmethod
    def get_api_base() -> URL:
        return API_BASE

    @staticmethod
    def get_auth_base() -> URL:
        return API_BASE / "app" / "getAppAccessToken"

    async def startup(self) -> None:
        if self._running:
            return
        if self._webhooks and not self._route_registered:
            self.setup_http_server(
                HTTPServerSetup(
                    URL("/qq/webhook"), "POST", "QQ Webhook", self._handle_http
                )
            )
            self._route_registered = True
        self._running = True
        for gateway in self._accounts.values():
            if gateway.account.transport == "websocket":
                task = asyncio.create_task(self._run_account(gateway))
                self._workers.add(task)
                task.add_done_callback(self._workers.discard)

    async def shutdown(self) -> None:
        self._running = False
        tasks = (*self._workers, *self._events)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for gateway in tuple(self.gateways.values()):
            self.disconnect(gateway)
        for gateway in self._accounts.values():
            gateway.clear_token()
            gateway.audit_result.close()

    async def _run_account(self, gateway: Gateway) -> None:
        while self._running:
            try:
                info = await gateway.shard_url_get()
                url = URL(info.url)
                if url.scheme != "wss" or not url.host or url.user is not None:
                    raise ValueError("Invalid QQ gateway URL")
                shards = (
                    [gateway.account.shard]
                    if gateway.account.shard
                    else [(index, info.shards) for index in range(info.shards)]
                )
                if not shards or info.session_start_limit.remaining < len(
                    shards
                ):
                    raise ValueError("QQ gateway session quota unavailable")
                async with asyncio.TaskGroup() as group:
                    for offset, shard in enumerate(shards):
                        # Stagger initial IDENTIFY across shards.
                        group.create_task(
                            self._run_shard(gateway, url, shard, 5 * offset)
                        )
            except Exception as exc:
                log(
                    "WARNING",
                    f"{gateway.id} gateway discovery failed ({type(exc).__name__})",
                )
            await asyncio.sleep(5)

    async def _run_shard(
        self, gateway: Gateway, url: URL, shard: tuple[int, int], delay: int
    ) -> None:
        session = _Session()
        await asyncio.sleep(delay)
        retry = 3
        while self._running:
            try:
                request = Request(
                    "GET", url, timeout=Timeout(connect=30, read=None, close=10)
                )
                async with self.websocket(request) as ws:
                    await self._run_socket(gateway, shard, session, ws)
            except Exception as exc:
                log(
                    "WARNING",
                    f"{gateway.id} shard {shard[0]} disconnected ({type(exc).__name__})",
                )
            await asyncio.sleep(retry)
            retry = min(retry * 2, 60) if session.id is None else 3

    async def _run_socket(
        self,
        gateway: Gateway,
        shard: tuple[int, int],
        session: _Session,
        ws: WebSocket,
    ) -> None:
        session.awaiting_ack = False
        async with asyncio.timeout(HANDSHAKE_TIMEOUT):
            hello = await self.receive_payload(ws)
            if (
                not isinstance(hello, Hello)
                or hello.data.heartbeat_interval <= 0
            ):
                raise ValueError("Invalid QQ Hello")
            token = await gateway._get_authorization_header()
            resumable = session.id is not None and session.sequence is not None
            auth: Payload
            if resumable:
                auth = Resume(
                    data={
                        "token": token,
                        "session_id": session.id,
                        "seq": session.sequence,
                    }
                )
            else:
                auth = Identify(
                    data={
                        "token": token,
                        "intents": gateway.account.intent.to_int(),
                        "shard": shard,
                        "properties": {
                            "$os": sys.platform,
                            "$language": "python",
                            "$sdk": "OpsMesh",
                        },
                    }
                )
            await ws.send(self.payload_to_json(auth))
            payload = await self.receive_payload(ws)
            replay: list[Dispatch] = []
            while (
                resumable
                and isinstance(payload, Dispatch)
                and payload.type not in {"READY", "RESUMED"}
            ):
                # QQ can replay events before RESUMED; don't dispatch them until
                # authentication has completed on this socket.
                if len(replay) >= MAX_PENDING_EVENTS:
                    raise RuntimeError("QQ replay capacity exhausted")
                replay.append(payload)
                payload = await self.receive_payload(ws)
            if isinstance(payload, InvalidSession):
                session.id = session.sequence = None
                return
            if not isinstance(payload, Dispatch) or payload.type not in (
                {"READY", "RESUMED"} if resumable else {"READY"}
            ):
                raise ValueError("Unexpected QQ authentication response")
            if payload.type == "READY":
                ready = cast(ReadyEvent, self.payload_to_event(payload))
                session.id = ready.session_id
                gateway.self_info = ready.user

        active = self._online_shards.setdefault(gateway.id, set())
        if not active:
            self.connect(gateway)
        active.add(shard[0])
        try:
            for event in replay:
                self._consume(gateway, session, event)
            self._consume(gateway, session, payload)
            async with asyncio.TaskGroup() as group:
                heartbeat = group.create_task(
                    self._heartbeat(
                        ws, session, hello.data.heartbeat_interval / 1000
                    )
                )
                try:
                    while True:
                        payload = await self.receive_payload(ws)
                        if isinstance(payload, InvalidSession):
                            session.id = session.sequence = None
                            break
                        if isinstance(payload, Reconnect):
                            break
                        if isinstance(payload, HeartbeatAck):
                            session.awaiting_ack = False
                        elif isinstance(payload, Dispatch):
                            self._consume(gateway, session, payload)
                finally:
                    heartbeat.cancel()
        finally:
            active.remove(shard[0])
            if not active:
                del self._online_shards[gateway.id]
                self.disconnect(gateway)

    async def _heartbeat(
        self, ws: WebSocket, session: _Session, interval: float
    ) -> None:
        while True:
            await asyncio.sleep(interval)
            if session.awaiting_ack:
                raise TimeoutError("QQ heartbeat was not acknowledged")
            session.awaiting_ack = True
            # Send null if QQ has not supplied a sequence yet.
            raw = (
                self.payload_to_json(Heartbeat(data=session.sequence))
                if session.sequence is not None
                else '{"op":1,"d":null}'
            )
            await asyncio.wait_for(ws.send(raw), timeout=interval)

    def _consume(
        self, gateway: Gateway, session: _Session, payload: Dispatch
    ) -> None:
        try:
            admitted = self.dispatch_event(gateway, payload)
        except ValueError:
            log("WARNING", f"{gateway.id} ignored malformed QQ event")
        else:
            if not admitted:
                raise RuntimeError("QQ event capacity exhausted")
        if payload.sequence is not None:
            session.sequence = payload.sequence

    async def _handle_http(self, request: Request) -> Response:
        if not self._running:
            return Response(503)
        gateway = self._webhooks.get(request.headers.get("X-Bot-Appid", ""))
        if gateway is None:
            return Response(403)
        body = request.content
        if isinstance(body, str):
            body = body.encode()
        if not isinstance(body, bytes) or not body:
            return Response(400)
        if len(body) > MAX_WEBHOOK_BYTES:
            return Response(413)
        try:
            payload = type_validate_json(PayloadType, body)
        except ValueError:
            return Response(400)
        key = Ed25519PrivateKey.from_private_bytes(
            (gateway._secret.encode() * 32)[:32]
        )
        if isinstance(payload, WebhookVerify):
            data = payload.data
            signature = key.sign((data.event_ts + data.plain_token).encode())
            return Response(
                200,
                headers={"Content-Type": "application/json"},
                content=json.dumps(
                    {
                        "plain_token": data.plain_token,
                        "signature": signature.hex(),
                    }
                ),
            )
        if not isinstance(payload, Dispatch):
            return Response(400)
        timestamp = request.headers.get("X-Signature-Timestamp", "")
        signature_hex = request.headers.get("X-Signature-Ed25519", "")
        if not timestamp or len(signature_hex) != 128:
            return Response(403)
        try:
            key.public_key().verify(
                bytes.fromhex(signature_hex), timestamp.encode() + body
            )
        except (ValueError, InvalidSignature):
            return Response(403)
        try:
            return Response(
                200
                if self.dispatch_event(gateway, payload, webhook=True)
                else 503
            )
        except ValueError:
            return Response(400)

    @staticmethod
    async def receive_payload(ws: WebSocket) -> Payload:
        return type_validate_json(PayloadType, await ws.receive())

    @staticmethod
    def payload_to_json(payload: Payload) -> str:
        if PYDANTIC_V2:
            return payload.model_dump_json(by_alias=True)
        return payload.json(by_alias=True)

    @staticmethod
    def payload_to_event(payload: Dispatch) -> Event | None:
        event_class = EVENT_CLASSES.get(payload.type)
        if event_class is None:
            return None
        if not isinstance(payload.data, dict):
            raise ValueError("Expected QQ event object")
        return type_validate_python(
            event_class, {**payload.data, "event_id": payload.id}
        )

    def dispatch_event(
        self, gateway: Gateway, payload: Dispatch, *, webhook: bool = False
    ) -> bool:
        event = self.payload_to_event(payload)
        if event is None:
            return True
        if not self._running or len(self._events) >= MAX_PENDING_EVENTS:
            return False
        if webhook and gateway.id not in self.gateways:
            self.connect(gateway)
        if isinstance(event, MessageAuditEvent):
            gateway.audit_result.add_result(event)
        task = asyncio.create_task(self._dispatch(gateway, event))
        self._events.add(task)
        task.add_done_callback(self._events.discard)
        return True

    async def _dispatch(self, gateway: Gateway, event: Event) -> None:
        try:
            await gateway.handle_event(event)
        except Exception as exc:
            log(
                "WARNING",
                f"{gateway.id} event dispatch failed ({type(exc).__name__})",
            )

    async def _call_api(
        self, gateway: BaseGateway, api: str, **data: Any
    ) -> Any:
        if (
            not isinstance(gateway, Gateway)
            or gateway.adapter is not self
            or not self._running
        ):
            raise ApiNotAvailable
        handler = getattr(type(gateway), api, None)
        if not isinstance(handler, API):
            raise ApiNotAvailable
        return await handler(gateway, **data)
