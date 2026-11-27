"""Minimal HTTP Webhook Adapter demonstrating OpsMesh external protocol bridging."""

import json
from typing import Any
from typing_extensions import override

from opsmesh.adapters import Adapter, Event, Gateway, Message, MessageSegment
from opsmesh.drivers import (
    URL,
    ASGIMixin,
    Driver,
    HTTPServerSetup,
    Request,
    Response,
)
from opsmesh.exception import ActionNotAvailable
from opsmesh.message import handle_event


class TextSegment(MessageSegment["WebhookMessage"]):
    @classmethod
    @override
    def get_message_class(cls) -> type["WebhookMessage"]:
        return WebhookMessage

    @override
    def __str__(self) -> str:
        return self.data["text"]

    @override
    def is_text(self) -> bool:
        return True


class WebhookMessage(Message[TextSegment]):
    @classmethod
    @override
    def get_segment_class(cls) -> type[TextSegment]:
        return TextSegment

    @staticmethod
    @override
    def _construct(msg: str):
        yield TextSegment("text", {"text": msg})


class WebhookEvent(Event):
    event_type: str = "message"
    sender_id: str = "anonymous"
    channel_id: str = "default"
    content: str = ""
    direct: bool = True

    @override
    def get_type(self) -> str:
        return self.event_type

    @override
    def get_event_name(self) -> str:
        return f"webhook.{self.event_type}"

    @override
    def get_event_description(self) -> str:
        return f"[{self.channel_id}/{self.sender_id}] {self.content}"

    @override
    def get_user_id(self) -> str:
        return self.sender_id

    @override
    def get_session_id(self) -> str:
        return f"{self.channel_id}:{self.sender_id}"

    @override
    def get_message(self) -> WebhookMessage:
        return WebhookMessage(self.content)

    @override
    def is_targeted(self) -> bool:
        return self.direct


class WebhookGateway(Gateway):
    def __init__(self, adapter: Adapter, id: str):
        super().__init__(adapter, id)
        self.outbox: dict[str, list[str]] = {}

    @override
    async def send(self, event: Event, message: Any, **kwargs: Any) -> Any:
        text = str(message)
        self.outbox.setdefault(event.get_session_id(), []).append(text)
        return await self.invoke(
            "emit_reply",
            session_id=event.get_session_id(),
            text=text,
            **kwargs,
        )


class WebhookAdapter(Adapter):
    @override
    def __init__(self, driver: Driver, **kwargs: Any):
        super().__init__(driver, **kwargs)
        self.gateway = WebhookGateway(self, "webhook:main")
        self.dispatched_actions: list[tuple[str, dict[str, Any]]] = []
        self._setup()

    @classmethod
    @override
    def get_name(cls) -> str:
        return "webhook"

    def _setup(self) -> None:
        if not isinstance(self.driver, ASGIMixin):
            raise TypeError("WebhookAdapter requires an ASGI driver (e.g. ~fastapi)")

        self.setup_http_server(
            HTTPServerSetup(
                path=URL("/webhook/events"),
                method="POST",
                name="webhook_events",
                handle_func=self._handle_http,
            )
        )
        self.driver.on_startup(self._startup)
        self.driver.on_shutdown(self._shutdown)

    async def _startup(self) -> None:
        self.gateway.connect()

    async def _shutdown(self) -> None:
        if self.gateway.id in self.gateways:
            self.gateway.disconnect()

    async def _handle_http(self, request: Request) -> Response:
        raw = request.content or b"{}"
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        event = WebhookEvent.model_validate(payload)

        session_id = event.get_session_id()
        self.gateway.outbox[session_id] = []
        await handle_event(self.gateway, event)
        replies = self.gateway.outbox.pop(session_id, [])

        return Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps({"replies": replies}, ensure_ascii=False).encode(),
        )

    @override
    async def _invoke(self, gateway: Gateway, action: str, **params: Any) -> Any:
        self.dispatched_actions.append((action, params))
        if action in {"emit_reply", "trigger_deploy", "record_alert"}:
            return {"ok": True, "gateway": gateway.id, "action": action, **params}
        raise ActionNotAvailable(self.get_name(), action)
