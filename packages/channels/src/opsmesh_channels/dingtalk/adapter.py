"""DingTalk robot callback verification, dispatch, and replies."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from functools import partial
from types import ModuleType
from typing import Any

import anyio
import httpx

from opsmesh.framework.adapters import Adapter
from opsmesh.framework.dispatch import dispatch_event
from opsmesh.framework.drivers import (
    URL,
    ASGIMixin,
    HTTPServerSetup,
    Request,
    Response,
)
from opsmesh.framework.plugins.registry import (
    _current_plugin,
    get_plugin_by_module_name,
    track_registration,
)
from opsmesh_channels._queue import EventQueue
from opsmesh_channels._validation import connection_segment
from opsmesh_channels.context import UserContext

from .channel import DingTalkChannel, outgoing_text
from .client import DingTalkClient
from .config import DingTalkConnectionSettings, DingTalkSettings
from .errors import (
    DingTalkConfigurationError,
    DingTalkError,
    DingTalkProtocolError,
    DingTalkSendError,
)
from .events import DingTalkMessageEvent, parse_callback
from .security import verify_signature

logger = logging.getLogger(__name__)

_WEBHOOK_SAFETY_MS = 5000


@dataclass(frozen=True)
class _WorkItem:
    """One verified event waiting for framework dispatch."""

    channel: DingTalkChannel
    event: DingTalkMessageEvent
    caller: UserContext


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
        status_code,
        headers=merged,
        content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )


class DingTalkAdapter(Adapter):
    """Manage declared DingTalk robot connections for one host driver."""

    def __init__(
        self,
        driver,
        *,
        settings: DingTalkSettings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create the adapter without opening network resources.

        Args:
            driver: The framework driver carrying the HTTP server.
            settings: Shared DingTalk protocol settings.
            http_client: Optional client used by tests or the host.
        """
        super().__init__(driver)
        self.settings = settings
        self._client = DingTalkClient(settings, http_client)
        self._declared: dict[str, DingTalkChannel] = {}
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
        return "dingtalk"

    @property
    def started(self) -> bool:
        """Whether callbacks are being served."""
        return self._started

    @property
    def declarations(self) -> tuple[DingTalkChannel, ...]:
        """Return a snapshot of declared connections."""
        return tuple(self._declared.values())

    def declare(
        self,
        settings: DingTalkConnectionSettings,
        *,
        name: str,
        owner: Any = None,
    ) -> DingTalkChannel:
        """Declare a robot connection owned by the calling plugin.

        Declaration validates and namespaces the connection but performs no
        network I/O. The connection opens during the adapter startup.
        """
        plugin = self._resolve_owner(owner)
        owner_id = plugin.id_
        connection_segment(owner_id, "DingTalk connection owner")
        connection_segment(name, "DingTalk connection name")
        self_id = f"dingtalk:{owner_id}:{name}"
        if self_id in self._declared:
            raise DingTalkConfigurationError(
                f"Duplicate DingTalk connection {self_id}"
            )

        path = f"{self.settings.path_prefix}/{owner_id}/{name}"
        channel = DingTalkChannel(
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
        if self._started:
            self._activate(channel)
        return channel

    async def startup(self) -> None:
        """Open shared resources and activate declared connections."""
        if self._started:
            return
        if not isinstance(self.driver, ASGIMixin):
            raise DingTalkConfigurationError(
                "The DingTalk channel requires an HTTP server driver"
            )
        if not self.driver.started:
            raise DingTalkConfigurationError(
                "The DingTalk channel requires a running driver lifespan"
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
                    logger.exception(
                        "Failed to roll back DingTalk adapter startup"
                    )
            raise

    def stop_accepting(self) -> None:
        """Stop inbound callbacks while keeping reply resources available."""
        self._queue.stop_accepting()
        for channel in list(self._declared.values()):
            self._unmount(channel)

    async def drain(self) -> None:
        """Finish accepted events before consumer plugins close."""
        await self._queue.stop()

    async def shutdown(self) -> None:
        """Stop callbacks, drain queued events, and release resources."""
        if not self._started:
            return
        self.stop_accepting()
        await self.drain()
        self._started = False
        for channel in list(self._declared.values()):
            if channel.self_id in self.channels:
                try:
                    self.disconnect(channel)
                except Exception:
                    logger.exception(
                        "Failed to disconnect DingTalk connection %s",
                        channel.self_id,
                    )
            channel.mark_closed()
        self._secrets.clear()
        await self._client.close()

    async def _call(
        self, channel: DingTalkChannel, operation: str, **data: Any
    ) -> Any:
        if self._declared.get(channel.self_id) is not channel:
            raise DingTalkError(
                "Channel does not belong to this DingTalk adapter"
            )
        if operation == "send_message":
            return await self._send_message(
                channel, data.get("event"), data.get("message")
            )
        raise DingTalkError(
            f"Unsupported DingTalk channel operation: {operation}"
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
            raise DingTalkConfigurationError(
                "A DingTalk connection must be declared by a loaded plugin"
            )
        return plugin

    def _activate(self, channel: DingTalkChannel) -> None:
        self._register_route(channel)
        try:
            secret = self._read_secret(channel)
        except DingTalkConfigurationError as error:
            channel.mark_failed(str(error))
            logger.error(
                "DingTalk connection %s is unavailable: %s",
                channel.self_id,
                error,
            )
            return
        self._secrets[channel.self_id] = secret
        channel.mark_ready()
        self.connect(channel)

    def _remove(self, channel: DingTalkChannel) -> None:
        self._declared.pop(channel.self_id, None)
        self._secrets.pop(channel.self_id, None)
        if self._started:
            self._unmount(channel)
            if channel.self_id in self.channels:
                try:
                    self.disconnect(channel)
                except Exception:
                    logger.exception(
                        "Failed to disconnect DingTalk connection %s",
                        channel.self_id,
                    )
        channel.mark_closed()

    def _read_secret(self, channel: DingTalkChannel) -> str:
        reference = channel.settings.client_secret_ref
        secret = os.environ.get(reference)
        if secret is None or not secret.strip():
            raise DingTalkConfigurationError(
                f"Environment variable {reference} is not set"
            )
        return secret.strip()

    def _register_route(self, channel: DingTalkChannel) -> None:
        setup = HTTPServerSetup(
            URL(channel.path),
            "POST",
            channel.self_id,
            partial(self._handle_callback, channel),
        )
        try:
            self.setup_http_server(setup)
        except (ValueError, RuntimeError) as error:
            channel.mark_failed("callback route is unavailable")
            raise DingTalkConfigurationError(
                f"The DingTalk callback route {channel.path} is unavailable"
            ) from error

    def _unmount(self, channel: DingTalkChannel) -> None:
        if not isinstance(self.driver, ASGIMixin):
            return
        try:
            self.driver.remove_http_server("POST", channel.path)
        except NotImplementedError:
            logger.warning(
                "The HTTP driver cannot remove DingTalk route %s",
                channel.path,
            )
        except Exception:
            logger.exception(
                "Failed to remove DingTalk callback route %s", channel.path
            )

    async def _handle_callback(
        self, channel: DingTalkChannel, request: Request
    ) -> Response:
        if not channel.ready:
            return _json_response(503, {"error": "connection unavailable"})
        if not self._signature_valid(channel, request):
            return _json_response(401, {"error": "invalid signature"})
        try:
            payload = json.loads(request.content)
        except ValueError:
            return _json_response(400, {"error": "invalid payload"})
        if not isinstance(payload, dict):
            return _json_response(400, {"error": "invalid payload"})
        try:
            event = parse_callback(payload, connection_id=channel.self_id)
        except DingTalkProtocolError:
            return _json_response(400, {"error": "invalid payload"})
        if event is None:
            return _json_response(200, {"status": "ignored"})
        if not channel.owner_ready:
            return _json_response(503, {"error": "plugin unavailable"})
        if not channel.first_delivery(event.message_id):
            return _json_response(200, {"status": "duplicate"})

        caller = UserContext(
            id=event.get_user_id(),
            name=event.user_name or event.get_user_id(),
            groups=event.group_ids,
        )
        accepted = self._queue.put_nowait(_WorkItem(channel, event, caller))
        if not accepted:
            logger.warning(
                "Rejecting DingTalk event %s for %s: queue is full",
                event.message_id,
                channel.self_id,
            )
            channel.release_delivery(event.message_id)
            return _json_response(503, {"error": "queue is full"})
        return _json_response(200, {"status": "ok"})

    def _signature_valid(
        self, channel: DingTalkChannel, request: Request
    ) -> bool:
        timestamp = request.headers.get("timestamp")
        signature = request.headers.get("sign")
        if not timestamp or not signature:
            return False
        try:
            issued = int(timestamp) / 1000
        except ValueError:
            return False
        if abs(time.time() - issued) > self.settings.max_clock_skew:
            return False
        secret = self._secrets.get(channel.self_id)
        if secret is None:
            return False
        return verify_signature(secret, timestamp, signature)

    async def _dispatch(self, item: _WorkItem) -> None:
        try:
            result = await dispatch_event(
                item.channel,
                item.event,
                invocation_context=item.caller,
                matcher_filter=item.channel.accepts_matcher,
            )
        except Exception:
            logger.exception(
                "DingTalk event %s dispatch failed", item.event.message_id
            )
            return
        if result.errors:
            logger.error(
                "DingTalk event %s dispatch produced %d error(s)",
                item.event.message_id,
                len(result.errors),
            )

    async def _send_message(
        self, channel: DingTalkChannel, event: Any, message: Any
    ) -> Any:
        if not channel.ready:
            raise DingTalkSendError(
                f"DingTalk connection {channel.self_id} is not ready"
            )
        if not isinstance(event, DingTalkMessageEvent):
            raise DingTalkSendError(
                "DingTalk replies require a DingTalkMessageEvent"
            )
        webhook = event.session_webhook
        if webhook is None:
            raise DingTalkSendError(
                "DingTalk replies require a session webhook"
            )
        expires = event.session_webhook_expires_at
        if expires is not None:
            remaining_ms = expires - int(time.time() * 1000)
            if remaining_ms <= _WEBHOOK_SAFETY_MS:
                raise DingTalkSendError(
                    "The DingTalk session webhook has expired"
                )
        text = outgoing_text(message, channel.settings.max_message_length)
        data = await self._client.post_json(
            webhook, {"msgtype": "text", "text": {"content": text}}
        )
        errcode = data.get("errcode", 0)
        if errcode not in (0, None):
            raise DingTalkSendError(
                f"DingTalk reply failed with code {errcode}"
            )
        return data
