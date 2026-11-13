"""HTTP requests and replies carried through the framework event pipeline."""

from typing import Any

from pydantic import ConfigDict, Field, PrivateAttr
from starlette.requests import Request

from opsmesh.adapters import Event
from opsmesh.dependencies import Dependent

from ..attachments import AttachmentCollection
from ..messages import Message


class HTTPEvent(Event):
    """A validated transport request, without agent or session semantics."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    request: Request
    payload: Any
    attachments: AttachmentCollection = Field(
        default_factory=AttachmentCollection, exclude=True
    )
    installation_id: str = ""
    route_name: str
    user_id: str
    _replied: bool = PrivateAttr(default=False)
    _result: Any = PrivateAttr(default=None)

    def get_type(self) -> str:
        """Return the request protocol, independently of business fields."""
        return "http"

    def get_event_name(self) -> str:
        """Return the server-registered entrance name."""
        return self.route_name

    def get_event_description(self) -> str:
        """Describe the entrance without exposing body or credentials."""
        return self.route_name

    def get_user_id(self) -> str:
        """Return the identity established by transport authentication."""
        return self.user_id

    def get_session_id(self) -> str:
        """Leave conversation identity to the owning plugin."""
        raise ValueError("HTTP requests do not define conversation sessions")

    def get_message(self) -> Message:
        """Reject message injection for ordinary HTTP requests."""
        raise ValueError("HTTP requests do not contain framework messages")

    def is_tome(self) -> bool:
        """HTTP routes explicitly address their registered handler."""
        return True

    @property
    def replied(self) -> bool:
        """Whether a handler has supplied the single response."""
        return self._replied

    @property
    def result(self) -> Any:
        """The plugin-selected result or response stream."""
        return self._result

    def reply(self, result: Any) -> None:
        """Accept one final response, including a streaming response handle."""
        if self._replied:
            raise RuntimeError("An HTTP event can only receive one response")
        self._result = result
        self._replied = True


class APIMessageEvent(HTTPEvent):
    """A standard API message delivered to existing framework matchers."""

    message: Message

    def get_type(self) -> str:
        """Keep image-only and text messages in the same event family."""
        return "message"

    def get_message(self) -> Message:
        """Return the normalized message."""
        return self.message


class HTTPHandler(Dependent[Any]):
    """Capture a Matcher handler's return value as the HTTP event reply."""

    async def __call__(self, **kwargs: Any) -> Any:
        """Run framework dependencies and capture the final reply."""
        result = await super().__call__(**kwargs)
        event: HTTPEvent = kwargs["event"]
        if not event.replied:
            event.reply(result)
        elif result is not None:
            raise RuntimeError(
                "A handler cannot both send and return a response"
            )
        return result
