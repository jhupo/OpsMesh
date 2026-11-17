"""HTTP requests and replies carried through the framework event pipeline."""

import json
from typing import Any
from uuid import uuid4

from pydantic import ConfigDict, Field, PrivateAttr
from starlette.requests import Request

from opsmesh.adapters import Event

from ..attachments import AttachmentCollection
from ..messages import Message
from .messages import MessageRequest


class APIMessageEvent(Event):
    """A standard API message delivered to existing framework matchers."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    request: Request
    payload: MessageRequest
    attachments: AttachmentCollection = Field(
        default_factory=AttachmentCollection,
        exclude=True,
    )
    installation_id: str
    route_name: str
    user_id: str
    message: Message
    conversation_id: str = Field(default_factory=lambda: str(uuid4()))
    connection_id: str = "api"
    _output: Any = PrivateAttr(default=None)

    def get_session_id(self) -> str:
        """Bind continuations to the authenticated transport conversation."""
        return json.dumps(
            [
                "api",
                self.connection_id,
                self.installation_id,
                self.user_id,
                self.conversation_id,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    @property
    def output(self) -> Any:
        """Return the live request output sink."""
        if self._output is None:
            raise RuntimeError("The message response is not active")
        return self._output

    def bind_output(self, output: Any) -> None:
        """Associate this event with exactly one request output scope."""
        if self._output is not None:
            raise RuntimeError("The message response is already bound")
        self._output = output

    def get_type(self) -> str:
        """Keep image-only and text messages in the same event family."""
        return "message"

    def get_message(self) -> Message:
        """Return the normalized message."""
        return self.message

    def get_event_name(self) -> str:
        """Identify the plugin's bound message entrance."""
        return self.route_name

    def get_event_description(self) -> str:
        """Describe the entrance without exposing the request content."""
        return self.route_name

    def get_user_id(self) -> str:
        """Use the identity verified by transport authentication."""
        return self.user_id

    def is_tome(self) -> bool:
        """The client explicitly addressed this message endpoint."""
        return True
