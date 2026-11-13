"""QQ official bot callback verification, dispatch, and vendor calls."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from functools import partial
from types import ModuleType
from typing import Any

import anyio
import httpx
from fastapi import FastAPI, Request
from starlette.responses import Response

from opsmesh.adapters import Adapter
from opsmesh.dispatch import dispatch_event
from opsmesh.drivers import Driver
from opsmesh.plugins.channels._queue import EventQueue
from opsmesh.plugins.channels._validation import connection_segment
from opsmesh.plugins.channels.context import UserContext
from opsmesh.plugins.model import Plugin, PluginStatus
from opsmesh.plugins.registry import (
    _current_plugin,
    get_plugin_by_module_name,
    track_registration,
)

from ..attachments import AttachmentCollection, AttachmentError
from ..operations import UnsupportedOperation
from ..requests import read_body
from ..server import Server
from .attachments import read as read_attachment
from .channel import QQChannel
from .client import QQClient
from .config import QQConnectionSettings, QQSettings
from .errors import (
    QQConfigurationError,
    QQError,
    QQProtocolError,
    QQSendError,
)
from .events import QQMessageEvent, QQNoticeEvent, parse_callback
from .groups import QQGroups
from .security import QQSigner
from .sending import QQSender

logger = logging.getLogger(__name__)

_PLAIN_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class _WorkItem:
    """One verified event waiting for framework dispatch."""

    channel: QQChannel
    event: QQMessageEvent | QQNoticeEvent
    caller: UserContext | None
    generation: int = field(init=False)

    def __post_init__(self) -> None:
        owner = self.channel._owner
        object.__setattr__(
            self,
            "generation",
            owner.generation if isinstance(owner, Plugin) else 0,
        )


def _json_response(
    status_code: int,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    merged = {"content-type": "application/json; charset=utf-8"}
    if headers:
        merged.update(headers)
    return Response(
        status_code=status_code,
        headers=merged,
        content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )


class QQAdapter(Adapter):
    """Manage declared QQ bot connections for one framework runtime."""

    def __init__(
        self,
        driver: Driver,
        *,
        settings: QQSettings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create the adapter without opening network resources.

        Args:
            driver: The protocol-independent framework driver.
            settings: Shared QQ protocol settings.
            http_client: Optional client used by tests or the host.
        """
        super().__init__(driver)
        self.app = FastAPI(
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
            redirect_slashes=False,
        )
        self.server = Server(self.app, settings)
        self._routes = {}
        self.settings = settings
        self._client = QQClient(settings, http_client)
        self._declared: dict[str, QQChannel] = {}
        self._signers: dict[str, QQSigner] = {}
        self._secrets: dict[str, str] = {}
        self._queue = EventQueue(
            capacity=settings.max_pending_events,
            concurrency=settings.concurrency,
            shutdown_timeout=settings.shutdown_timeout,
            handle=self._dispatch,
        )
        self._started = False

    @classmethod
    def get_name(cls) -> str:
        """Return the adapter name used by channel types."""
        return "qq"

    @property
    def started(self) -> bool:
        """Whether callbacks are being served."""
        return self._started

    @property
    def declarations(self) -> tuple[QQChannel, ...]:
        """Return a snapshot of declared connections."""
        return tuple(self._declared.values())

    def declare(
        self,
        settings: QQConnectionSettings,
        *,
        name: str,
        owner: Any = None,
    ) -> QQChannel:
        """Declare a bot connection owned by the calling plugin.

        Declaration validates and namespaces the connection but performs no
        network I/O. The connection opens during the adapter startup.
        """
        plugin = self._resolve_owner(owner)
        owner_id = plugin.id_
        connection_segment(owner_id, "QQ connection owner")
        connection_segment(name, "QQ connection name")
        self_id = f"qq:{owner_id}:{name}"
        if self_id in self._declared:
            raise QQConfigurationError(f"Duplicate QQ connection {self_id}")

        path = f"{self.settings.path_prefix}/{owner_id}/{name}"
        channel = QQChannel(
            self,
            self_id,
            owner_id=owner_id,
            owner=plugin,
            connection_name=name,
            settings=settings,
            protocol=self.settings,
            path=path,
        )
        self._declared[self_id] = channel
        track_registration(lambda: self._remove(channel), owner)
        if isinstance(plugin, Plugin):
            self.driver.on_ready(
                lambda: self._activate(channel) if self._started else None
            )
            self.driver.on_shutdown(lambda: self._deactivate(channel))
        if self._started:
            self._activate(channel)
        return channel

    async def startup(self) -> None:
        """Open shared resources and activate declared connections."""
        if self._started:
            return
        if get_plugin_by_module_name(__name__) is not None:
            from .config import get_settings

            if self.settings != get_settings():
                raise QQConfigurationError(
                    "Shared QQ protocol settings changed; restart the host"
                )
        if not self.driver.started:
            raise QQConfigurationError(
                "The QQ channel requires a running runtime lifespan"
            )
        self._started = True
        self._client.open()
        try:
            await self._queue.start(self.driver.task_group)
            for channel in list(self._declared.values()):
                self._activate(channel)
        except BaseException:
            with anyio.CancelScope(shield=True):
                try:
                    await self.shutdown()
                except BaseException:
                    logger.exception("Failed to roll back QQ adapter startup")
            raise

    async def start_accepting(self) -> None:
        """Open the QQ callback listener after consumer readiness."""
        await self.server.startup(self.driver.task_group)

    def stop_accepting(self) -> None:
        """Stop inbound callbacks while keeping reply resources available."""
        self.server.stop_accepting()
        self._queue.stop_accepting()
        for channel in list(self._declared.values()):
            self._unmount(channel)

    async def drain(self) -> None:
        """Finish accepted events before consumer plugins close."""
        await self.server.shutdown()
        await self._queue.stop()

    async def shutdown(self) -> None:
        """Stop callbacks, drain queued events, and release resources."""
        if not self._started:
            return
        self.stop_accepting()
        await self.drain()
        for channel in list(self._declared.values()):
            if channel.self_id in self.channels:
                try:
                    self.disconnect(channel)
                except Exception:
                    logger.exception(
                        "Failed to disconnect QQ connection %s",
                        channel.self_id,
                    )
            channel.mark_closed()
        self._signers.clear()
        self._secrets.clear()
        await self._client.close()
        self._started = False

    async def _call(
        self, channel: QQChannel, operation: str, **data: Any
    ) -> Any:
        if self._declared.get(channel.self_id) is not channel:
            raise QQError("Channel does not belong to this QQ adapter")
        if operation == "send_message":
            return await self._send_message(
                channel, data.get("event"), data.get("message")
            )
        if operation in QQGroups.operations:
            from opsmesh.plugins.registry import get_current_plugin

            if (
                isinstance(channel._owner, Plugin)
                and get_current_plugin() is not channel._owner
            ):
                raise PermissionError("QQ connection belongs to another plugin")
            if not channel.ready:
                raise QQSendError("QQ connection is not ready")
            groups = QQGroups(self._client, channel, self._read_secret(channel))
            return await getattr(groups, operation)(**data)
        raise UnsupportedOperation(
            f"Unsupported QQ channel operation: {operation}"
        )

    def _resolve_owner(self, owner: Any):
        plugin = _current_plugin.get()
        if plugin is None and owner is not None:
            module_name = (
                owner.__name__
                if isinstance(owner, ModuleType)
                else getattr(owner, "__module__", "")
            )
            plugin = get_plugin_by_module_name(module_name)
        if plugin is None:
            raise QQConfigurationError(
                "A QQ connection must be declared by a loaded plugin"
            )
        return plugin

    def _activate(self, channel: QQChannel) -> None:
        if channel.self_id in self.channels:
            return
        owner = channel._owner
        if isinstance(owner, Plugin) and owner.status not in (
            PluginStatus.STARTING,
            PluginStatus.READY,
        ):
            return
        self._register_route(channel)
        try:
            secret = self._read_secret(channel)
            signer = QQSigner(secret)
        except QQConfigurationError as error:
            channel.mark_failed(str(error))
            logger.error(
                "QQ connection %s is unavailable: %s",
                channel.self_id,
                error,
            )
            return
        self._signers[channel.self_id] = signer
        self._secrets[channel.self_id] = secret
        channel.mark_ready()
        self.connect(channel)

    def _remove(self, channel: QQChannel) -> None:
        self._declared.pop(channel.self_id, None)
        self._deactivate(channel)

    def _deactivate(self, channel: QQChannel) -> None:
        self._signers.pop(channel.self_id, None)
        self._secrets.pop(channel.self_id, None)
        self._client.forget(channel.self_id)
        if self._started:
            self._unmount(channel)
            if channel.self_id in self.channels:
                try:
                    self.disconnect(channel)
                except Exception:
                    logger.exception(
                        "Failed to disconnect QQ connection %s",
                        channel.self_id,
                    )
        channel.mark_closed()

    def _read_secret(self, channel: QQChannel) -> str:
        reference = channel.settings.secret_ref
        secret = os.environ.get(reference)
        if secret is None or not secret.strip():
            raise QQConfigurationError(
                f"Environment variable {reference} is not set"
            )
        return secret.strip()

    def _register_route(self, channel: QQChannel) -> None:
        if any(
            getattr(route, "path", None) == channel.path
            for route in self.app.router.routes
        ):
            raise QQConfigurationError("The QQ callback route is unavailable")

        async def callback(request: Request) -> Response:
            return await self._handle_callback(channel, request)

        self.app.add_api_route(
            channel.path, callback, methods=["POST"], name=channel.self_id
        )
        self._routes[channel.self_id] = self.app.router.routes[-1]

    def _unmount(self, channel: QQChannel) -> None:
        route = self._routes.pop(channel.self_id, None)
        if route is not None:
            self.app.router.routes.remove(route)

    async def _handle_callback(
        self, channel: QQChannel, request: Request
    ) -> Response:
        if not channel.ready:
            return _json_response(503, {"error": "connection unavailable"})
        signer = self._signers.get(channel.self_id)
        if signer is None:
            return _json_response(503, {"error": "connection unavailable"})
        body = await read_body(request, self.settings.max_request_body)
        try:
            payload = json.loads(body)
        except ValueError:
            return _json_response(400, {"error": "invalid payload"})
        if not isinstance(payload, dict):
            return _json_response(400, {"error": "invalid payload"})

        # QQ sends the URL validation challenge without signature headers.
        if payload.get("op") == 13:
            return self._handshake(signer, payload)
        if not self._signature_valid(signer, request, body):
            return _json_response(401, {"error": "invalid signature"})
        if payload.get("op") != 0:
            return _json_response(200, {"status": "ignored"})
        try:
            event = parse_callback(
                payload,
                connection_id=channel.self_id,
                attachments=AttachmentCollection(self.settings.attachments),
                reader=partial(
                    read_attachment, settings=self.settings.attachments
                ),
            )
        except (QQProtocolError, AttachmentError):
            return _json_response(400, {"error": "invalid payload"})
        if event is None:
            return _json_response(200, {"status": "ignored"})
        if not channel.owner_ready:
            return _json_response(503, {"error": "plugin unavailable"})
        if not channel.first_delivery(event.event_id):
            return _json_response(200, {"status": "duplicate"})

        caller = (
            UserContext(
                id=event.get_user_id(),
                name=event.user_name or event.get_user_id(),
                groups=event.group_ids,
            )
            if isinstance(event, QQMessageEvent)
            else None
        )
        accepted = self._queue.put_nowait(_WorkItem(channel, event, caller))
        if not accepted:
            logger.warning(
                "Rejecting QQ event %s for %s: queue is full",
                event.event_id,
                channel.self_id,
            )
            channel.release_delivery(event.event_id)
            return _json_response(503, {"error": "queue is full"})
        return _json_response(200, {"status": "ok"})

    def _signature_valid(
        self, signer: QQSigner, request: Request, body: bytes
    ) -> bool:
        signature = request.headers.get("x-signature-ed25519")
        timestamp = request.headers.get("x-signature-timestamp")
        if not signature or not timestamp:
            return False
        try:
            issued = int(timestamp)
        except ValueError:
            return False
        if abs(time.time() - issued) > self.settings.max_clock_skew:
            return False
        return signer.verify(signature, timestamp, body)

    def _handshake(self, signer: QQSigner, payload: dict[str, Any]) -> Response:
        data = payload.get("d")
        if not isinstance(data, dict):
            return _json_response(400, {"error": "invalid payload"})
        token = data.get("plain_token")
        event_ts = data.get("event_ts")
        if not isinstance(token, str) or not isinstance(event_ts, str):
            return _json_response(400, {"error": "invalid payload"})
        # Restrict the unsigned challenge input so it cannot act as a
        # signing oracle for arbitrary callback bodies.
        if not _PLAIN_TOKEN.fullmatch(token) or not event_ts.isdigit():
            return _json_response(400, {"error": "invalid payload"})
        if abs(time.time() - int(event_ts)) > self.settings.max_clock_skew:
            return _json_response(400, {"error": "invalid payload"})
        return _json_response(
            200,
            {
                "plain_token": token,
                "signature": signer.sign(event_ts + token),
            },
        )

    async def _dispatch(self, item: _WorkItem) -> None:
        owner = item.channel._owner
        expired = (
            isinstance(owner, Plugin) and owner.generation != item.generation
        )
        if expired or not item.channel.owner_ready or not item.channel.ready:
            logger.info(
                "Cancelled queued QQ event %s: owner stopped",
                item.event.message_id,
            )
            return
        try:
            result = await dispatch_event(
                item.channel,
                item.event,
                invocation_context=item.caller,
                matcher_filter=item.channel.accepts_matcher,
            )
        except Exception:
            logger.exception("QQ event %s dispatch failed", item.event.event_id)
            return
        if result.errors:
            logger.error(
                "QQ event %s dispatch produced %d error(s)",
                item.event.event_id,
                len(result.errors),
            )

    async def _send_message(
        self, channel: QQChannel, event: Any, message: Any
    ) -> Any:
        if not channel.ready:
            raise QQSendError(f"QQ connection {channel.self_id} is not ready")
        if not isinstance(event, QQMessageEvent):
            raise QQSendError("QQ replies require a QQMessageEvent")
        if event.connection_id != channel.self_id:
            raise QQSendError("QQ reply event belongs to another connection")
        return await QQSender(
            self._client, channel, self._secrets[channel.self_id]
        ).send(event, message)
