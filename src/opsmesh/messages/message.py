"""Typed message segments and mutable message sequences."""

import abc
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import (  # noqa: UP035
    Any,
    Generic,
    Self,
    SupportsIndex,
    Type,
    TypeVar,
    overload,
)

from pydantic import TypeAdapter

from opsmesh.compat import custom_validation

from .template import MessageTemplate

TMS = TypeVar("TMS", bound="MessageSegment")
TM = TypeVar("TM", bound="Message")


@custom_validation
@dataclass
class MessageSegment(abc.ABC, Generic[TM]):
    """Base class for a typed message segment."""

    type: str
    """The segment type."""
    data: dict[str, Any] = field(default_factory=dict)
    """The segment payload."""

    @classmethod
    @abc.abstractmethod
    def get_message_class(cls) -> Type[TM]:  # noqa: UP006
        """Return the corresponding message sequence class."""
        raise NotImplementedError

    @abc.abstractmethod
    def __str__(self) -> str:
        """Return the segment text used for command matching."""
        raise NotImplementedError

    def __len__(self) -> int:
        return len(str(self))

    def __ne__(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, other: Self
    ) -> bool:
        return not self == other

    def __add__(self, other: str | Self | Iterable[Self]) -> TM:
        return self.get_message_class()(self) + other

    def __radd__(self, other: str | Self | Iterable[Self]) -> TM:
        return self.get_message_class()(other) + self

    @classmethod
    def __get_validators__(cls):
        yield cls._validate

    @classmethod
    def _validate(cls, value) -> Self:
        if isinstance(value, cls):
            return value
        if isinstance(value, MessageSegment):
            raise ValueError(
                f"Type {type(value)} can not be converted to {cls}"
            )
        if not isinstance(value, dict):
            raise ValueError(
                f"Expected dict for MessageSegment, got {type(value)}"
            )
        if "type" not in value:
            raise ValueError(
                f"Expected dict with 'type' for MessageSegment, got {value}"
            )
        return cls(type=value["type"], data=value.get("data", {}))

    def get(self, key: str, default: Any = None):
        """Read a serialized segment field, falling back to the default."""
        return asdict(self).get(key, default)

    def keys(self):
        """Return the serialized segment field names."""
        return asdict(self).keys()

    def values(self):
        """Return deep-copied values of the serialized segment fields."""
        return asdict(self).values()

    def items(self):
        """Return serialized field names paired with deep-copied values."""
        return asdict(self).items()

    def join(self, iterable: Iterable[Self | TM]) -> TM:
        """Join messages using this segment as the separator."""
        return self.get_message_class()(self).join(iterable)

    def copy(self) -> Self:
        """Return a deep copy of this segment and its payload."""
        return deepcopy(self)

    @abc.abstractmethod
    def is_text(self) -> bool:
        """Return whether the segment contains plain text."""
        raise NotImplementedError


@custom_validation
class Message(list[TMS], abc.ABC):
    """A mutable sequence of message segments.

    Args:
        message: Initial message content.
    """

    def __init__(
        self,
        message: str | Iterable[TMS] | TMS | None = None,
    ):
        super().__init__()
        if message is None:
            return
        elif isinstance(message, str):
            self.extend(self._construct(message))
        elif isinstance(message, MessageSegment):
            self.append(message)
        elif isinstance(message, Iterable):
            self.extend(message)
        else:
            self.extend(self._construct(message))  # pragma: no cover

    @classmethod
    def template(cls, format_string: str | TM) -> MessageTemplate[Self]:
        """Create a message template.

        Formatting resembles str.format, but accepts and returns message
        objects.
        Additional format specifiers can construct segments through
        factory methods on the associated MessageSegment class.

        Args:
            format_string: The formatting template.

        Returns:
            A message template formatter.
        """
        return MessageTemplate(format_string, cls)

    @classmethod
    @abc.abstractmethod
    def get_segment_class(cls) -> type[TMS]:
        """Return the corresponding message segment class."""
        raise NotImplementedError

    def __str__(self) -> str:
        return "".join(str(seg) for seg in self)

    @classmethod
    def __get_validators__(cls):
        yield cls._validate

    @classmethod
    def _validate(cls, value) -> Self:
        if isinstance(value, cls):
            return value
        elif isinstance(value, Message):
            raise ValueError(
                f"Type {type(value)} can not be converted to {cls}"
            )
        elif isinstance(value, str):
            pass
        elif isinstance(value, dict):
            value = TypeAdapter(cls.get_segment_class()).validate_python(value)
        elif isinstance(value, Iterable):
            adapter = TypeAdapter(cls.get_segment_class())
            value = [adapter.validate_python(segment) for segment in value]
        else:
            raise ValueError(
                f"Expected str, dict or iterable for Message, got {type(value)}"
            )
        return cls(value)

    @staticmethod
    @abc.abstractmethod
    def _construct(msg: str) -> Iterable[TMS]:
        """Construct message segments from input content."""
        raise NotImplementedError

    def __add__(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, other: str | TMS | Iterable[TMS]
    ) -> Self:
        result = self.copy()
        result += other
        return result

    def __radd__(self, other: str | TMS | Iterable[TMS]) -> Self:
        result = self.__class__(other)
        return result + self

    def __iadd__(self, other: str | TMS | Iterable[TMS]) -> Self:
        if isinstance(other, str):
            self.extend(self._construct(other))
        elif isinstance(other, MessageSegment):
            self.append(other)
        elif isinstance(other, Iterable):
            self.extend(other)
        else:
            raise TypeError(f"Unsupported type {type(other)!r}")
        return self

    @overload
    def __getitem__(self, args: str) -> Self: ...

    @overload
    def __getitem__(self, args: tuple[str, int]) -> TMS: ...

    @overload
    def __getitem__(self, args: tuple[str, slice]) -> Self: ...

    @overload
    def __getitem__(self, args: int) -> TMS: ...

    @overload
    def __getitem__(self, args: slice) -> Self: ...

    def __getitem__(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        args: str | tuple[str, int] | tuple[str, slice] | int | slice,
    ) -> TMS | Self:
        """Select segments by position, type, or a type with a position.

        Args:
            args: An integer selects one segment; a slice returns a message.
                A string filters by segment type. A (type, index) or
                (type, slice) tuple indexes the filtered segments.

        Returns:
            A segment for integer selections, or a message for other forms.
        """
        arg1, arg2 = args if isinstance(args, tuple) else (args, None)
        if isinstance(arg1, int) and arg2 is None:
            return super().__getitem__(arg1)
        elif isinstance(arg1, slice) and arg2 is None:
            return self.__class__(super().__getitem__(arg1))
        elif isinstance(arg1, str) and arg2 is None:
            return self.__class__(seg for seg in self if seg.type == arg1)
        elif isinstance(arg1, str) and isinstance(arg2, int):
            return [seg for seg in self if seg.type == arg1][arg2]
        elif isinstance(arg1, str) and isinstance(arg2, slice):
            return self.__class__(
                [seg for seg in self if seg.type == arg1][arg2]
            )
        else:
            raise ValueError("Incorrect arguments to slice")  # pragma: no cover

    def __contains__(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, value: TMS | str
    ) -> bool:
        """Check whether a segment or segment type occurs in the message.

        Args:
            value: A message segment or segment type.

        Returns:
            Whether the message contains the segment or a segment of that
            type.
        """
        if isinstance(value, str):
            return (
                next((seg for seg in self if seg.type == value), None)
                is not None
            )
        return super().__contains__(value)

    def has(self, value: TMS | str) -> bool:
        """Return the same membership result as __contains__."""
        return value in self

    def index(self, value: TMS | str, *args: SupportsIndex) -> int:
        """Return the first matching index within the requested bounds.

        Args:
            value: A message segment or segment type.
            *args: Optional start and end bounds, using list.index semantics.

        Returns:
            The matching segment index.

        Raises:
            ValueError: No matching segment exists within the requested
                bounds.
        """
        if isinstance(value, str):
            return [segment.type for segment in self].index(value, *args)
        return super().index(value, *args)

    def get(self, type_: str, count: int | None = None) -> Self:
        """Select a limited number of segments of a given type.

        Args:
            type_: The segment type.
            count: The maximum number of segments to select.

        Returns:
            A new message containing the selected segments.
        """
        if count is None:
            return self[type_]

        iterator, filtered = (
            (seg for seg in self if seg.type == type_),
            self.__class__(),
        )
        for _ in range(count):
            seg = next(iterator, None)
            if seg is None:
                break
            filtered.append(seg)
        return filtered

    def count(self, value: TMS | str) -> int:
        """Count matching segments or segments of a given type.

        Args:
            value: A message segment or segment type.

        Returns:
            The number of matches.
        """
        return (
            len(self[value]) if isinstance(value, str) else super().count(value)
        )

    def only(self, value: TMS | str) -> bool:
        """Check whether every segment matches the requested value or type.

        Args:
            value: The required segment or segment type.

        Returns:
            Whether every segment matches.
        """
        if isinstance(value, str):
            return all(seg.type == value for seg in self)
        return all(seg == value for seg in self)

    def append(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, obj: str | TMS
    ) -> Self:
        """Append one segment to the message.

        Args:
            obj: The segment to append.
        """
        if isinstance(obj, MessageSegment):
            super().append(obj)
        elif isinstance(obj, str):
            self.extend(self._construct(obj))
        else:
            raise ValueError(
                f"Unexpected type: {type(obj)} {obj}"
            )  # pragma: no cover
        return self

    def extend(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, obj: Self | Iterable[TMS]
    ) -> Self:
        """Extend the message with another message or iterable of segments.

        Args:
            obj: The message or segments to append.
        """
        for segment in obj:
            self.append(segment)
        return self

    def join(self, iterable: Iterable[TMS | Self]) -> Self:
        """Join messages using this message as the separator.

        Args:
            iterable: The messages to join.

        Returns:
            The joined message.
        """
        ret = self.__class__()
        for index, msg in enumerate(iterable):
            if index != 0:
                ret.extend(self)
            if isinstance(msg, MessageSegment):
                ret.append(msg.copy())
            else:
                ret.extend(msg.copy())
        return ret

    def copy(self) -> Self:
        """Return a deep copy of the message."""
        return deepcopy(self)

    def include(self, *types: str) -> Self:
        """Filter message segments by type.

        Args:
            types: Segment types to include.

        Returns:
            A newly constructed message.
        """
        return self.__class__(seg for seg in self if seg.type in types)

    def exclude(self, *types: str) -> Self:
        """Filter message segments by type.

        Args:
            types: Segment types to exclude.

        Returns:
            A newly constructed message.
        """
        return self.__class__(seg for seg in self if seg.type not in types)

    def extract_plain_text(self) -> str:
        """Extract plain-text segments from the message."""
        return "".join(str(seg) for seg in self if seg.is_text())
