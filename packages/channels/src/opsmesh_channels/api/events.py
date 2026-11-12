"""HTTP requests and replies carried through the framework event pipeline."""

from typing import Any

from pydantic import ConfigDict, PrivateAttr

from opsmesh.framework.adapters import Event
from opsmesh.framework.dependencies import Dependent
from opsmesh.framework.drivers import Request
from opsmesh.framework.messages import Message

from .messages import APIMessage


class HTTPEvent(Event):
    """A validated transport request, without agent or session semantics."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    request: Request
    payload: Any
    message: APIMessage
    route_name: str
    user_id: str
    _replied: bool = PrivateAttr(default=False)
    _result: Any = PrivateAttr(default=None)

    def get_type(self) -> str:
        """Allow framework message rules when the input contains text."""
        return (
            "message"
            if any(part.is_text() for part in self.message)
            else "http"
        )

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
        """Return the normalized framework message."""
        return self.message

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
