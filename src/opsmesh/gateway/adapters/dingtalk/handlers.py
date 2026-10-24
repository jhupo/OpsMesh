import json
from typing import Any

from dingtalk_stream import (
    AckMessage,
    CallbackHandler,
    ChatbotMessage,
    EventHandler,
    GraphHandler,
    GraphMessage,
    GraphRequest,
)
from opsmesh_sdk.channels import EventSink

from .config import DingTalkConfig
from .events import callback, chatbot


class RobotHandler(CallbackHandler):
    def __init__(self, sink: EventSink):
        super().__init__()
        self.sink = sink

    async def process(self, frame: Any) -> tuple[int, str]:
        await self.sink(chatbot(frame))
        return AckMessage.STATUS_OK, "OK"


class SubscriptionHandler(EventHandler):
    def __init__(self, sink: EventSink):
        super().__init__()
        self.sink = sink

    async def process(self, frame: Any) -> tuple[int, str]:
        await self.sink(callback(frame, "event"))
        return AckMessage.STATUS_OK, "OK"


class CardHandler(CallbackHandler):
    def __init__(self, sink: EventSink, response: dict[str, Any]):
        super().__init__()
        self.sink, self.response = sink, response

    async def process(self, frame: Any) -> tuple[int, str]:
        await self.sink(callback(frame, "card"))
        return AckMessage.STATUS_OK, json.dumps(self.response, ensure_ascii=False)


class GraphCallbackHandler(GraphHandler):
    def __init__(self, sink: EventSink):
        super().__init__()
        self.sink = sink

    async def process(self, frame: Any) -> tuple[int, str]:
        GraphRequest.from_dict(frame.data)
        event = callback(frame, "graph")
        await self.sink(event)
        response = self.get_success_response({"event_id": event.id, "status": "recorded"})
        # A durable asynchronous ACK, never a fabricated synchronous business result.
        response.status_line.code, response.status_line.reason_phrase = 202, "Accepted"
        return AckMessage.STATUS_OK, json.dumps(response.to_dict(), ensure_ascii=False)


def register(client: Any, config: DingTalkConfig, sink: EventSink, logger: Any) -> None:
    handlers: list[Any] = []
    robot = RobotHandler(sink)
    client.register_callback_handler(ChatbotMessage.TOPIC, robot)
    client.register_callback_handler(ChatbotMessage.DELEGATE_TOPIC, robot)
    handlers.append(robot)
    if config.subscribe_events:
        handler = SubscriptionHandler(sink)
        client.register_all_event_handler(handler)
        handlers.append(handler)
    if config.card_callbacks:
        handler = CardHandler(sink, config.card_callback_response)
        client.register_callback_handler(CallbackHandler.TOPIC_CARD_CALLBACK, handler)
        handlers.append(handler)
    if config.graph_callbacks:
        handler = GraphCallbackHandler(sink)
        client.register_callback_handler(GraphMessage.TOPIC, handler)
        handlers.append(handler)
    for handler in handlers:
        handler.logger = logger
