"""Channel operations and invocation hooks."""

import abc
import logging
from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

import anyio

from opsmesh.framework.config import Config
from opsmesh.framework.exceptions import MockCallException
from opsmesh.framework.typing import T_CalledHook, T_CallingHook
from opsmesh.framework.utils import flatten_exception_group

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from opsmesh.framework.messages.message import Message, MessageSegment

    from .adapter import Adapter
    from .event import Event

    class _OperationCall(Protocol):
        async def __call__(self, **kwargs: Any) -> Any: ...


class Channel(abc.ABC):
    """Base interface for an adapter-managed channel.

    A logical endpoint exposing operation calls and message sending.

    Args:
        adapter: The protocol adapter instance.
        self_id: Channel ID
    """

    _calling_hook: ClassVar[set[T_CallingHook]] = set()
    """Hooks executed before an operation call."""
    _called_hook: ClassVar[set[T_CalledHook]] = set()
    """Hooks executed after an operation call."""

    def __init__(self, adapter: "Adapter", self_id: str):
        self.adapter: Adapter = adapter
        """The protocol adapter instance."""
        self.self_id: str = self_id
        """Channel ID."""

    def __repr__(self) -> str:
        return f"Channel(type={self.type!r}, self_id={self.self_id!r})"

    def __getattr__(self, name: str) -> "_OperationCall":
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{name}'"
            )
        return partial(self.call, name)

    @property
    def type(self) -> str:
        """The protocol adapter name."""
        return self.adapter.get_name()

    @property
    def config(self) -> Config:
        """The global framework configuration."""
        return self.adapter.config

    async def call(self, operation: str, **data: Any) -> Any:
        """Call an operation, also accessible as a dynamic channel attribute.

        Args:
            operation: The operation name.
            data: Keyword arguments for the operation.

        Examples:
            ```python
            await channel.call("send_msg", message="hello world")
            await channel.send_msg(message="hello world")
            ```
        """
        result: Any = None
        skip_calling: bool = False
        exception: Exception | None = None

        if self._calling_hook:
            logger.debug("Running Calling hooks...")

            try:
                async with anyio.create_task_group() as tg:
                    for hook in self._calling_hook:
                        tg.start_soon(hook, self, operation, data)
            except* MockCallException as exc_group:
                excs = list(flatten_exception_group(exc_group))
                if len(excs) > 1:
                    logger.warning(
                        "Multiple hooks mock the result; using the first."
                    )

                skip_calling = True
                result = excs[0].result
                logger.debug("Operation %s was replaced by a hook", operation)
            except* Exception as exc_group:
                for exc in flatten_exception_group(exc_group):
                    logger.error(
                        "Error when running Calling hook. Running cancelled!",
                        exc_info=(type(exc), exc, exc.__traceback__),
                    )

        if not skip_calling:
            try:
                result = await self.adapter._call(self, operation, **data)
            except Exception as e:
                exception = e

        if self._called_hook:
            logger.debug("Running Called hooks...")

            try:
                async with anyio.create_task_group() as tg:
                    for hook in self._called_hook:
                        tg.start_soon(
                            hook, self, exception, operation, data, result
                        )
            except* MockCallException as exc_group:
                excs = list(flatten_exception_group(exc_group))
                if len(excs) > 1:
                    logger.warning(
                        "Multiple hooks mock the result; using the first."
                    )

                result = excs[0].result
                exception = None
                logger.debug(
                    "Operation %s result was replaced by a hook", operation
                )
            except* Exception as exc_group:
                for exc in flatten_exception_group(exc_group):
                    logger.error(
                        "Error when running Called hook. Running cancelled!",
                        exc_info=(type(exc), exc, exc.__traceback__),
                    )

        if exception:
            raise exception
        return result

    @abc.abstractmethod
    async def send(
        self,
        event: "Event",
        message: "str | Message | MessageSegment",
        **kwargs: Any,
    ) -> Any:
        """Send a message using the channel's basic messaging interface.

        Args:
            event: The incoming event.
            message: The message to send.
            kwargs: Additional keyword arguments.
        """
        raise NotImplementedError

    @classmethod
    def on_calling(cls, func: T_CallingHook) -> T_CallingHook:
        """Register a hook executed before an operation call.

        Hook arguments:

        channel: The current channel instance.
        operation: The operation being invoked.
        data: The operation argument mapping.
        """
        from opsmesh.framework.plugins.registry import register_callback

        return register_callback(cls._calling_hook, func)

    @classmethod
    def on_called(cls, func: T_CalledHook) -> T_CalledHook:
        """Register a hook executed after an operation call.

        Hook arguments:

        channel: The current channel instance.
        exception: The error raised by the operation, if any.
        operation: The operation being invoked.
        data: The operation argument mapping.
        result: The operation's return value.
        """
        from opsmesh.framework.plugins.registry import register_callback

        return register_callback(cls._called_hook, func)
