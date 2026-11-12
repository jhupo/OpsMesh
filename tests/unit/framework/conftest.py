from types import SimpleNamespace

import pytest

from opsmesh.adapters import Channel, Event
from opsmesh.config import Config
from opsmesh.drivers import Driver
from opsmesh.messages import Message, MessageSegment


class TextSegment(MessageSegment):
    @classmethod
    def get_message_class(cls):
        return TextMessage

    def __str__(self):
        return self.data["text"]

    def is_text(self):
        return True


class TextMessage(Message[TextSegment]):
    @classmethod
    def get_segment_class(cls):
        return TextSegment

    @staticmethod
    def _construct(message):
        yield TextSegment("text", {"text": message})


class MemoryChannel(Channel):
    async def send(self, event, message, **kwargs):
        return None


class MemoryEvent(Event):
    def get_type(self):
        return "message"

    def get_event_name(self):
        return "memory.message"

    def get_event_description(self):
        return "message"

    def get_user_id(self):
        return "user"

    def get_session_id(self):
        return "connection:user:conversation"

    def get_message(self):
        return TextMessage("answer")

    def is_tome(self):
        return True


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def runtime_type():
    return Driver


@pytest.fixture
def conversation():
    channel = MemoryChannel(
        SimpleNamespace(config=Config(), get_name=lambda: "memory"),
        "connection",
    )
    return channel, MemoryEvent()
