"""HTTP requests and replies carried through the framework event pipeline."""

from typing import Any

from pydantic import PrivateAttr

from ..events import MessageEvent


class APIMessageEvent(MessageEvent):
    """A standard API message delivered to existing framework matchers."""

    EVENT_NAME = "api.message"

    _output: Any = PrivateAttr(default=None)

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
