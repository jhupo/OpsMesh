"""Exceptions used by the framework runtime.

Some exceptions control internal execution and are handled by the framework.

```bash
FrameworkException
├── ParserExit
├── ProcessException
|   ├── IgnoredException
|   ├── SkippedException
|   |   └── TypeMisMatch
|   └── StopPropagation
├── MatcherException
|   ├── PausedException
|   ├── RejectedException
|   └── FinishedException
└── NoLogException
```
"""

from typing import Any

from opsmesh.compat import ModelField


class FrameworkException(Exception):
    """Base class for framework exceptions."""

    def __str__(self) -> str:
        return self.__repr__()


# Rule Exception
class ParserExit(FrameworkException):
    """A shell-command parser failure."""

    def __init__(self, status: int = 0, message: str | None = None) -> None:
        self.status = status
        self.message = message

    def __repr__(self) -> str:
        return (
            f"ParserExit(status={self.status}"
            + (f", message={self.message!r}" if self.message else "")
            + ")"
        )


# Processor Exception
class ProcessException(FrameworkException):
    """Base class for event-processing exceptions."""


class IgnoredException(ProcessException):
    """Signal that an event preprocessor wants to ignore this event.

    Args:
        reason: The reason for ignoring the event.
    """

    def __init__(self, reason: Any) -> None:
        self.reason: Any = reason

    def __repr__(self) -> str:
        return f"IgnoredException(reason={self.reason!r})"


class SkippedException(ProcessException):
    """Stop execution of the current dependency or handler.

    Handlers can raise this through Matcher.skip.

    Examples:
        ```python
        def always_skip():
            Matcher.skip()

        @matcher.handle()
        async def handler(dependency = Depends(always_skip)):
            # never run
        ```
    """


class TypeMisMatch(SkippedException):
    """A handler argument does not match its declared type."""

    def __init__(self, param: ModelField, value: Any) -> None:
        self.param: ModelField = param
        self.value: Any = value

    def __repr__(self) -> str:
        return (
            f"TypeMisMatch(param={self.param.name}, "
            f"type={self.param._type_display()}, value={self.value!r}>"
        )


class StopPropagation(ProcessException):
    """Stop propagation of the current event to lower-priority matchers.

    Raised when Matcher.block is True
    or when Matcher.stop_propagation is called.

    Examples:
        ```python
        matcher = on_notice(block=True)


        # Alternatively, install the dependency directly.
        @matcher.handle()
        async def handler(matcher: Matcher):
            matcher.stop_propagation()
        ```
    """


# Matcher Exceptions
class MatcherException(FrameworkException):
    """Base class for matcher control-flow exceptions."""


class PausedException(MatcherException):
    """Pause processing until a new message arrives, then run the next handler.

    Use this when more input is needed from the user.

    Handlers can raise this through Matcher.pause.

    Examples:
        ```python
        @matcher.handle()
        async def handler():
            await matcher.pause("some message")
        ```
    """


class RejectedException(MatcherException):
    """Wait for another message, then rerun the current handler.

    Use this to request replacement input from the user.

    Handlers can raise this through Matcher.reject.

    Examples:
        ```python
        @matcher.handle()
        async def handler():
            await matcher.reject("some message")
        ```
    """


class FinishedException(MatcherException):
    """Finish the current handler and skip all remaining handlers.

    Handlers can raise this through Matcher.finish.

    Examples:
        ```python
        @matcher.handle()
        async def handler():
            await matcher.finish("some message")
        ```
    """


class NoLogException(FrameworkException):
    """Process the current event without emitting its normal event log.

    Event.get_log_string may raise this exception.
    """
