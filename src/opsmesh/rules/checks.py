"""Rule definitions for event matchers.

Each matcher owns a Rule instance
containing a collection of rule checkers.
Every checker must pass for the matcher to execute.
"""

import logging
import re
import shlex
from argparse import Action, ArgumentError
from argparse import ArgumentParser as ArgParser
from argparse import Namespace as Namespace
from collections.abc import Sequence
from contextvars import ContextVar
from gettext import gettext
from itertools import chain, product
from typing import (
    IO,
    TYPE_CHECKING,
    NamedTuple,
    TypedDict,
    TypeVar,
    cast,
    overload,
)

from pygtrie import CharTrie

from opsmesh.adapters import Channel, Event, Message, MessageSegment
from opsmesh.consts import (
    CMD_ARG_KEY,
    CMD_KEY,
    CMD_START_KEY,
    CMD_WHITESPACE_KEY,
    ENDSWITH_KEY,
    FULLMATCH_KEY,
    KEYWORD_KEY,
    PREFIX_KEY,
    RAW_CMD_KEY,
    REGEX_MATCHED,
    SHELL_ARGS,
    SHELL_ARGV,
    STARTSWITH_KEY,
)
from opsmesh.drivers import get_driver
from opsmesh.exceptions import ParserExit
from opsmesh.params import (
    Command,
    CommandArg,
    CommandWhitespace,
    EventToMe,
)
from opsmesh.rules.base import Rule as Rule
from opsmesh.typing import T_State

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CMD_RESULT(TypedDict):
    """Command prefix, normalized name, argument message, and whitespace."""

    command: tuple[str, ...] | None
    raw_command: str | None
    command_arg: Message | None
    command_start: str | None
    command_whitespace: str | None


class TRIE_VALUE(NamedTuple):
    """Command prefix metadata stored in the matching trie."""

    command_start: str
    command: tuple[str, ...]


parser_message: ContextVar[str] = ContextVar("parser_message")


class TrieRule:
    """Index command prefixes and populate parsed command state."""

    prefix: CharTrie = CharTrie()

    @classmethod
    def add_prefix(cls, prefix: str, value: TRIE_VALUE) -> None:
        """Register a command prefix, warning when it is already registered."""
        if prefix in cls.prefix:
            logger.warning(f'Duplicated prefix rule "{prefix}"')
            return
        cls.prefix[prefix] = value

    @classmethod
    def get_value(
        cls, channel: Channel, event: Event, state: T_State
    ) -> CMD_RESULT:
        """Extract the longest command prefix and store its parsed arguments."""
        prefix = CMD_RESULT(
            command=None,
            raw_command=None,
            command_arg=None,
            command_start=None,
            command_whitespace=None,
        )
        state[PREFIX_KEY] = prefix
        if event.get_type() != "message":
            return prefix

        message = event.get_message()
        message_seg: MessageSegment = message[0]
        if message_seg.is_text():
            segment_text = str(message_seg).lstrip()
            if pf := cls.prefix.longest_prefix(segment_text):
                value: TRIE_VALUE = pf.value
                prefix[RAW_CMD_KEY] = pf.key
                prefix[CMD_START_KEY] = value.command_start
                prefix[CMD_KEY] = value.command

                msg = message.copy()
                msg.pop(0)

                # check whitespace
                arg_str = segment_text[len(pf.key) :]
                arg_str_stripped = arg_str.lstrip()
                # check next segment until arg detected or no text remain
                while not arg_str_stripped and msg and msg[0].is_text():
                    arg_str += str(msg.pop(0))
                    arg_str_stripped = arg_str.lstrip()

                has_arg = arg_str_stripped or msg
                if (
                    has_arg
                    and (stripped_len := len(arg_str) - len(arg_str_stripped))
                    > 0
                ):
                    prefix[CMD_WHITESPACE_KEY] = arg_str[:stripped_len]

                # construct command arg
                if arg_str_stripped:
                    new_message = msg.__class__(arg_str_stripped)
                    for new_segment in reversed(new_message):
                        msg.insert(0, new_segment)
                prefix[CMD_ARG_KEY] = msg

        return prefix


class StartswithRule:
    """Check whether plain text starts with one of the specified prefixes.

    Args:
        msg: Allowed text prefixes.
        ignorecase: Whether matching ignores case.
    """

    __slots__ = ("ignorecase", "msg")

    def __init__(self, msg: tuple[str, ...], ignorecase: bool = False):
        self.msg = msg
        self.ignorecase = ignorecase

    def __repr__(self) -> str:
        return f"Startswith(msg={self.msg}, ignorecase={self.ignorecase})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, StartswithRule)
            and frozenset(self.msg) == frozenset(other.msg)
            and self.ignorecase == other.ignorecase
        )

    def __hash__(self) -> int:
        return hash((frozenset(self.msg), self.ignorecase))

    async def __call__(self, event: Event, state: T_State) -> bool:
        """Match the message prefix and store the matched text."""
        try:
            text = event.get_plaintext()
        except Exception:
            return False
        if match := re.match(
            f"^(?:{'|'.join(re.escape(prefix) for prefix in self.msg)})",
            text,
            re.IGNORECASE if self.ignorecase else 0,
        ):
            state[STARTSWITH_KEY] = match.group()
            return True
        return False


def startswith(msg: str | tuple[str, ...], ignorecase: bool = False) -> Rule:
    """Match a prefix of the message's plain text.

    Args:
        msg: Allowed text prefixes.
        ignorecase: Whether matching ignores case.
    """
    if isinstance(msg, str):
        msg = (msg,)

    return Rule(StartswithRule(msg, ignorecase))


class EndswithRule:
    """Check whether plain text ends with one of the specified suffixes.

    Args:
        msg: Allowed text suffixes.
        ignorecase: Whether matching ignores case.
    """

    __slots__ = ("ignorecase", "msg")

    def __init__(self, msg: tuple[str, ...], ignorecase: bool = False):
        self.msg = msg
        self.ignorecase = ignorecase

    def __repr__(self) -> str:
        return f"Endswith(msg={self.msg}, ignorecase={self.ignorecase})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, EndswithRule)
            and frozenset(self.msg) == frozenset(other.msg)
            and self.ignorecase == other.ignorecase
        )

    def __hash__(self) -> int:
        return hash((frozenset(self.msg), self.ignorecase))

    async def __call__(self, event: Event, state: T_State) -> bool:
        """Match the message suffix and store the matched text."""
        try:
            text = event.get_plaintext()
        except Exception:
            return False
        if match := re.search(
            f"(?:{'|'.join(re.escape(suffix) for suffix in self.msg)})$",
            text,
            re.IGNORECASE if self.ignorecase else 0,
        ):
            state[ENDSWITH_KEY] = match.group()
            return True
        return False


def endswith(msg: str | tuple[str, ...], ignorecase: bool = False) -> Rule:
    """Match a suffix of the message's plain text.

    Args:
        msg: Allowed text prefixes.
        ignorecase: Whether matching ignores case.
    """
    if isinstance(msg, str):
        msg = (msg,)

    return Rule(EndswithRule(msg, ignorecase))


class FullmatchRule:
    """Check whether plain text exactly matches one of the supplied strings.

    Args:
        msg: Allowed complete text values.
        ignorecase: Whether matching ignores case.
    """

    __slots__ = ("ignorecase", "msg")

    def __init__(self, msg: tuple[str, ...], ignorecase: bool = False):
        self.msg = tuple(map(str.casefold, msg) if ignorecase else msg)
        self.ignorecase = ignorecase

    def __repr__(self) -> str:
        return f"Fullmatch(msg={self.msg}, ignorecase={self.ignorecase})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, FullmatchRule)
            and frozenset(self.msg) == frozenset(other.msg)
            and self.ignorecase == other.ignorecase
        )

    def __hash__(self) -> int:
        return hash((frozenset(self.msg), self.ignorecase))

    async def __call__(self, event: Event, state: T_State) -> bool:
        """Match the complete message text and store the matched text."""
        try:
            text = event.get_plaintext()
        except Exception:
            return False
        if not text:
            return False
        text = text.casefold() if self.ignorecase else text
        if text in self.msg:
            state[FULLMATCH_KEY] = text
            return True
        return False


def fullmatch(msg: str | tuple[str, ...], ignorecase: bool = False) -> Rule:
    """Match the complete message text.

    Args:
        msg: Allowed complete text values.
        ignorecase: Whether matching ignores case.
    """
    if isinstance(msg, str):
        msg = (msg,)

    return Rule(FullmatchRule(msg, ignorecase))


class KeywordsRule:
    """Check whether plain text contains a supplied keyword.

    Args:
        keywords: The keywords to look for.
    """

    __slots__ = ("keywords",)

    def __init__(self, *keywords: str):
        self.keywords = keywords

    def __repr__(self) -> str:
        return f"Keywords(keywords={self.keywords})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, KeywordsRule) and frozenset(
            self.keywords
        ) == frozenset(other.keywords)

    def __hash__(self) -> int:
        return hash(frozenset(self.keywords))

    async def __call__(self, event: Event, state: T_State) -> bool:
        """Find a configured keyword and store the matching keyword."""
        try:
            text = event.get_plaintext()
        except Exception:
            return False
        if not text:
            return False
        if key := next((k for k in self.keywords if k in text), None):
            state[KEYWORD_KEY] = key
            return True
        return False


def keyword(*keywords: str) -> Rule:
    """Match keywords in the message's plain text.

    Args:
        keywords: The keywords to look for.
    """
    return Rule(KeywordsRule(*keywords))


class CommandRule:
    """Check whether a message starts with a specified command.

    Args:
        cmds: The command tuples to match.
        force_whitespace: Whether specific whitespace must follow the
            command.
    """

    __slots__ = ("cmds", "force_whitespace")

    def __init__(
        self,
        cmds: list[tuple[str, ...]],
        force_whitespace: str | bool | None = None,
    ):
        self.cmds = tuple(cmds)
        self.force_whitespace = force_whitespace

    def __repr__(self) -> str:
        return f"Command(cmds={self.cmds})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CommandRule) and frozenset(
            self.cmds
        ) == frozenset(other.cmds)

    def __hash__(self) -> int:
        return hash((frozenset(self.cmds),))

    async def __call__(
        self,
        cmd: tuple[str, ...] | None = Command(),
        cmd_arg: Message | None = CommandArg(),
        cmd_whitespace: str | None = CommandWhitespace(),
    ) -> bool:
        """Check the parsed command name and whitespace constraints."""
        if cmd not in self.cmds:
            return False
        if self.force_whitespace is None or not cmd_arg:
            return True
        if isinstance(self.force_whitespace, str):
            return self.force_whitespace == cmd_whitespace
        return self.force_whitespace == (cmd_whitespace is not None)


def command(
    *cmds: str | tuple[str, ...],
    force_whitespace: str | bool | None = None,
) -> Rule:
    """Match a command at the start of a message.

    Matching uses command_start and command_sep
    from the framework configuration.

    Use Command to retrieve the matched tuple, for example ("test",),
    RawCommand to retrieve the original command text, for example "/test",
    and CommandArg to retrieve the remaining message arguments.

    Args:
        cmds: Command strings or command tuples.
        force_whitespace: Whether specific whitespace must follow the
            command.

    Examples:
        With the default command_start and command_sep settings:

        The command ("test",) matches messages starting with /test.
        The command ("test", "sub") matches messages starting with /test.sub.

    Note:
    Whitespace between the command and its arguments is not required.
    """
    config = get_driver().config
    command_start = config.command_start
    command_sep = config.command_sep
    commands: list[tuple[str, ...]] = []
    for command in cmds:
        if isinstance(command, str):
            command = (command,)

        commands.append(command)

        if len(command) == 1:
            for start in command_start:
                TrieRule.add_prefix(
                    f"{start}{command[0]}", TRIE_VALUE(start, command)
                )
        else:
            for start, sep in product(command_start, command_sep):
                TrieRule.add_prefix(
                    f"{start}{sep.join(command)}", TRIE_VALUE(start, command)
                )

    return Rule(CommandRule(commands, force_whitespace))


class ArgumentParser(ArgParser):
    """A shell-style parser that raises parser errors instead of exiting.

    Supports rich Message input in addition to plain text.

    Examples:
        Usage follows argparse.ArgumentParser;
        see https://docs.python.org/3/library/argparse.html.
    """

    if TYPE_CHECKING:

        @overload
        def parse_known_args(
            self,
            args: Sequence[str | MessageSegment] | None = None,
            namespace: None = None,
        ) -> tuple[Namespace, list[str | MessageSegment]]: ...

        @overload
        def parse_known_args(
            self, args: Sequence[str | MessageSegment] | None, namespace: T
        ) -> tuple[T, list[str | MessageSegment]]: ...

        @overload
        def parse_known_args(
            self, *, namespace: T
        ) -> tuple[T, list[str | MessageSegment]]: ...

        def parse_known_args(  # pyright: ignore[reportIncompatibleMethodOverride]
            self,
            args: Sequence[str | MessageSegment] | None = None,
            namespace: T | None = None,
        ) -> tuple[Namespace | T, list[str | MessageSegment]]:
            """Parse supported options and retain unrecognized arguments."""
            ...

    @overload
    def parse_args(
        self,
        args: Sequence[str | MessageSegment] | None = None,
        namespace: None = None,
    ) -> Namespace: ...

    @overload
    def parse_args(
        self, args: Sequence[str | MessageSegment] | None, namespace: T
    ) -> T: ...

    @overload
    def parse_args(self, *, namespace: T) -> T: ...

    def parse_args(
        self,
        args: Sequence[str | MessageSegment] | None = None,
        namespace: T | None = None,
    ) -> Namespace | T:
        """Parse command arguments, reporting any unrecognized arguments."""
        result, argv = self.parse_known_args(args, namespace)
        if argv:
            msg = gettext("unrecognized arguments: %s")
            self.error(msg % " ".join(map(str, argv)))
        return cast(Namespace | T, result)

    def _parse_optional(
        self, arg_string: str | MessageSegment
    ) -> tuple[Action | None, str, str | None] | None:
        return (
            super()._parse_optional(arg_string)
            if isinstance(arg_string, str)
            else None
        )

    def _print_message(self, message: str, file: IO[str] | None = None):  # type: ignore
        if (msg := parser_message.get(None)) is not None:
            parser_message.set(msg + message)
        else:
            super()._print_message(message, file)

    def exit(self, status: int = 0, message: str | None = None):
        """Raise ParserExit instead of terminating the host process."""
        if message:
            self._print_message(message)
        raise ParserExit(status=status, message=parser_message.get(None))


class ShellCommandRule:
    """Check whether a message matches a specified shell-style command.

    Args:
        cmds: The command tuples to match.
        parser: An optional argument parser.
    """

    __slots__ = ("cmds", "parser")

    def __init__(
        self, cmds: list[tuple[str, ...]], parser: ArgumentParser | None
    ):
        self.cmds = tuple(cmds)
        self.parser = parser

    def __repr__(self) -> str:
        return f"ShellCommand(cmds={self.cmds}, parser={self.parser})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, ShellCommandRule)
            and frozenset(self.cmds) == frozenset(other.cmds)
            and self.parser is other.parser
        )

    def __hash__(self) -> int:
        return hash((frozenset(self.cmds), self.parser))

    async def __call__(
        self,
        state: T_State,
        cmd: tuple[str, ...] | None = Command(),
        msg: Message | None = CommandArg(),
    ) -> bool:
        """Parse matching command arguments into matcher state."""
        if cmd not in self.cmds or msg is None:
            return False

        try:
            state[SHELL_ARGV] = list(
                chain.from_iterable(
                    shlex.split(str(seg))
                    if cast(MessageSegment, seg).is_text()
                    else (seg,)
                    for seg in msg
                )
            )
        except Exception as e:
            # set SHELL_ARGV to opsmesh indicating shlex error
            state[SHELL_ARGV] = None
            # ensure SHELL_ARGS is set to ParserExit if parser is provided
            if self.parser:
                state[SHELL_ARGS] = ParserExit(status=2, message=str(e))
            return True

        if self.parser:
            t = parser_message.set("")
            try:
                args = self.parser.parse_args(state[SHELL_ARGV])
                state[SHELL_ARGS] = args
            except ArgumentError as e:
                state[SHELL_ARGS] = ParserExit(status=2, message=str(e))
            except ParserExit as e:
                state[SHELL_ARGS] = e
            finally:
                parser_message.reset(t)
        return True


def shell_command(
    *cmds: str | tuple[str, ...], parser: ArgumentParser | None = None
) -> Rule:
    """Match a shell-style command and parse its arguments.

    Matching uses command_start and command_sep
    from the framework configuration.

    Use Command to retrieve the matched command tuple,
    for example ("test",).
    Use RawCommand to retrieve the original command text,
    for example "/test".
    Use ShellCommandArgv to retrieve the unparsed argument list,
    for example ["arg", "-h"].
    Use ShellCommandArgs to retrieve parsed arguments,
    for example {"arg": "arg", "h": True}.

    Warning:
    If parsing fails, ShellCommandArgs provides
    the ParserExit exception instead of parsed arguments.

    Args:
        cmds: Command strings or command tuples.
        parser: An ArgumentParser instance.

    Examples:
        These examples use the default command_start and command_sep
        settings.
        See https://docs.python.org/3/library/argparse.html for more examples.

        ```python
        from opsmesh.rules import ArgumentParser

        parser = ArgumentParser()
        parser.add_argument("-a", action="store_true")

        rule = shell_command("ls", parser=parser)
        ```

    Note:
    Whitespace between the command and its arguments is not required.
    """
    if parser is not None and not isinstance(parser, ArgumentParser):
        raise TypeError("`parser` must be a framework ArgumentParser instance")

    config = get_driver().config
    command_start = config.command_start
    command_sep = config.command_sep
    commands: list[tuple[str, ...]] = []
    for command in cmds:
        if isinstance(command, str):
            command = (command,)

        commands.append(command)

        if len(command) == 1:
            for start in command_start:
                TrieRule.add_prefix(
                    f"{start}{command[0]}", TRIE_VALUE(start, command)
                )
        else:
            for start, sep in product(command_start, command_sep):
                TrieRule.add_prefix(
                    f"{start}{sep.join(command)}", TRIE_VALUE(start, command)
                )

    return Rule(ShellCommandRule(commands, parser))


class RegexRule:
    """Check whether the message string matches a regular expression.

    Args:
        regex: The regular-expression pattern.
        flags: Regular-expression flags.
    """

    __slots__ = ("flags", "regex")

    def __init__(self, regex: str, flags: int = 0):
        self.regex = regex
        self.flags = flags

    def __repr__(self) -> str:
        return f"Regex(regex={self.regex!r}, flags={self.flags})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, RegexRule)
            and self.regex == other.regex
            and self.flags == other.flags
        )

    def __hash__(self) -> int:
        return hash((self.regex, self.flags))

    async def __call__(self, event: Event, state: T_State) -> bool:
        """Search message text and store the regular-expression match."""
        try:
            msg = event.get_message()
        except Exception:
            return False
        if matched := re.search(self.regex, str(msg), self.flags):
            state[REGEX_MATCHED] = matched
            return True
        else:
            return False


def regex(regex: str, flags: int | re.RegexFlag = 0) -> Rule:
    """Match a regular expression against the message string.

    Use RegexStr to retrieve matched text,
    RegexGroup to retrieve positional groups,
    and RegexDict to retrieve named groups.

    Args:
        regex: The regular-expression pattern.
        flags: Regular-expression flags.

    Note:
    Matching uses search, not match; anchor with ^ to require a prefix match.

    Note:
    The expression operates on str(EventMessage),
    not on the message's extracted plain text.
    """
    return Rule(RegexRule(regex, flags))


class ToMeRule:
    """Check whether the event is directed to the channel."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "ToMe()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ToMeRule)

    def __hash__(self) -> int:
        return hash((self.__class__,))

    async def __call__(self, to_me: bool = EventToMe()) -> bool:
        """Return whether the event is directed to the channel."""
        return to_me


def to_me() -> Rule:
    """Match events directed to the channel."""
    return Rule(ToMeRule())


class IsTypeRule:
    """Check whether an event is an instance of a specified class."""

    __slots__ = ("types",)

    def __init__(self, *types: type[Event]):
        self.types = types

    def __repr__(self) -> str:
        return f"IsType(types={tuple(type.__name__ for type in self.types)})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, IsTypeRule) and self.types == other.types

    def __hash__(self) -> int:
        return hash((self.types,))

    async def __call__(self, event: Event) -> bool:
        """Return whether the event has one of the configured types."""
        return isinstance(event, self.types)


def is_type(*types: type[Event]) -> Rule:
    """Match instances of the supplied event classes.

    Args:
        types: The event classes to accept.
    """
    return Rule(IsTypeRule(*types))


__autodoc__ = {
    "Rule": True,
    "Rule.__call__": True,
    "TrieRule": False,
    "ArgumentParser.exit": False,
    "ArgumentParser.parse_args": False,
}
