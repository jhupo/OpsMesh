"""Dependency descriptors for event and matcher handler parameters."""

from collections.abc import Callable
from re import Match
from typing import Any, Literal, overload

from opsmesh.framework.adapters import Event, Message, MessageSegment
from opsmesh.framework.consts import (
    CMD_ARG_KEY,
    CMD_KEY,
    CMD_START_KEY,
    CMD_WHITESPACE_KEY,
    ENDSWITH_KEY,
    FULLMATCH_KEY,
    KEYWORD_KEY,
    PAUSE_PROMPT_RESULT_KEY,
    PREFIX_KEY,
    RAW_CMD_KEY,
    RECEIVE_KEY,
    REGEX_MATCHED,
    REJECT_PROMPT_RESULT_KEY,
    SHELL_ARGS,
    SHELL_ARGV,
    STARTSWITH_KEY,
)
from opsmesh.framework.dependencies.context import Context as Context
from opsmesh.framework.dependencies.params import Arg as Arg
from opsmesh.framework.dependencies.params import ArgParam as ArgParam
from opsmesh.framework.dependencies.params import ArgPlainText as ArgPlainText
from opsmesh.framework.dependencies.params import (
    ArgPromptResult as ArgPromptResult,
)
from opsmesh.framework.dependencies.params import ArgStr as ArgStr
from opsmesh.framework.dependencies.params import ChannelParam as ChannelParam
from opsmesh.framework.dependencies.params import DefaultParam as DefaultParam
from opsmesh.framework.dependencies.params import DependParam as DependParam
from opsmesh.framework.dependencies.params import Depends as Depends
from opsmesh.framework.dependencies.params import EventParam as EventParam
from opsmesh.framework.dependencies.params import (
    ExceptionParam as ExceptionParam,
)
from opsmesh.framework.dependencies.params import MatcherParam as MatcherParam
from opsmesh.framework.dependencies.params import StateParam as StateParam
from opsmesh.framework.matchers import Matcher
from opsmesh.framework.typing import T_State


async def _event_type(event: Event) -> str:
    return event.get_type()


def EventType() -> str:
    """Return a dependency descriptor for the event type."""
    return Depends(_event_type)


async def _event_message(event: Event) -> Message:
    return event.get_message()


def EventMessage() -> Any:
    """Return a dependency descriptor for the event message."""
    return Depends(_event_message)


async def _event_plain_text(event: Event) -> str:
    return event.get_plaintext()


def EventPlainText() -> str:
    """Return a dependency descriptor for the event's plain text."""
    return Depends(_event_plain_text)


async def _event_to_me(event: Event) -> bool:
    return event.is_tome()


def EventToMe() -> bool:
    """Inject whether the event targets the channel."""
    return Depends(_event_to_me)


def _command(state: T_State) -> Message:
    return state[PREFIX_KEY][CMD_KEY]


def Command() -> tuple[str, ...]:
    """Return a dependency descriptor for the matched command tuple."""
    return Depends(_command)


def _raw_command(state: T_State) -> Message:
    return state[PREFIX_KEY][RAW_CMD_KEY]


def RawCommand() -> str:
    """Return a dependency descriptor for the raw command text."""
    return Depends(_raw_command)


def _command_arg(state: T_State) -> Message:
    return state[PREFIX_KEY][CMD_ARG_KEY]


def CommandArg() -> Any:
    """Return a dependency descriptor for command arguments."""
    return Depends(_command_arg)


def _command_start(state: T_State) -> str:
    return state[PREFIX_KEY][CMD_START_KEY]


def CommandStart() -> str:
    """Return a dependency descriptor for the command start marker."""
    return Depends(_command_start)


def _command_whitespace(state: T_State) -> str:
    return state[PREFIX_KEY][CMD_WHITESPACE_KEY]


def CommandWhitespace() -> str:
    """Return a dependency descriptor for whitespace after the command."""
    return Depends(_command_whitespace)


def _shell_command_args(state: T_State) -> Any:
    return state[SHELL_ARGS]  # Namespace or ParserExit


def ShellCommandArgs() -> Any:
    """Return a dependency descriptor for parsed shell-command arguments."""
    return Depends(_shell_command_args, use_cache=False)


def _shell_command_argv(state: T_State) -> list[str | MessageSegment]:
    return state[SHELL_ARGV]


def ShellCommandArgv() -> Any:
    """Inject the raw shell-command argument list."""
    return Depends(_shell_command_argv, use_cache=False)


def _regex_matched(state: T_State) -> Match[str]:
    return state[REGEX_MATCHED]


def RegexMatched() -> Match[str]:
    """Return a dependency descriptor for the regular-expression match."""
    return Depends(_regex_matched, use_cache=False)


def _regex_str(
    groups: tuple[str | int, ...],
) -> Callable[[T_State], str | tuple[str | Any, ...] | Any]:
    def _regex_str_dependency(
        state: T_State,
    ) -> str | tuple[str | Any, ...] | Any:
        return _regex_matched(state).group(*groups)

    return _regex_str_dependency


@overload
def RegexStr(group: Literal[0] = 0, /) -> str: ...


@overload
def RegexStr(group: str | int, /) -> str | Any: ...


@overload
def RegexStr(
    group1: str | int, group2: str | int, /, *groups: str | int
) -> tuple[str | Any, ...]: ...


def RegexStr(*groups: str | int) -> str | tuple[str | Any, ...] | Any:
    """Return a dependency descriptor for matched regular-expression text."""
    return Depends(_regex_str(groups), use_cache=False)


def _regex_group(state: T_State) -> tuple[Any, ...]:
    return _regex_matched(state).groups()


def RegexGroup() -> tuple[Any, ...]:
    """Return a dependency descriptor for the matched group tuple."""
    return Depends(_regex_group, use_cache=False)


def _regex_dict(state: T_State) -> dict[str, Any]:
    return _regex_matched(state).groupdict()


def RegexDict() -> dict[str, Any]:
    """Return a dependency descriptor for the matched named-group mapping."""
    return Depends(_regex_dict, use_cache=False)


def _startswith(state: T_State) -> str:
    return state[STARTSWITH_KEY]


def Startswith() -> str:
    """Return a dependency descriptor for the prefix that triggered matching."""
    return Depends(_startswith, use_cache=False)


def _endswith(state: T_State) -> str:
    return state[ENDSWITH_KEY]


def Endswith() -> str:
    """Return a dependency descriptor for the suffix that triggered matching."""
    return Depends(_endswith, use_cache=False)


def _fullmatch(state: T_State) -> str:
    return state[FULLMATCH_KEY]


def Fullmatch() -> str:
    """Inject the full message that triggered matching."""
    return Depends(_fullmatch, use_cache=False)


def _keyword(state: T_State) -> str:
    return state[KEYWORD_KEY]


def Keyword() -> str:
    """Inject the keyword that triggered matching."""
    return Depends(_keyword, use_cache=False)


def Received(id: str | None = None, default: Any = None) -> Any:
    """Return a dependency descriptor for a received event."""

    def _received(matcher: "Matcher") -> Any:
        return matcher.get_receive(id or "", default)

    return Depends(_received, use_cache=False)


def LastReceived(default: Any = None) -> Any:
    """Return a dependency descriptor for the last received event."""

    def _last_received(matcher: "Matcher") -> Any:
        return matcher.get_last_receive(default)

    return Depends(_last_received, use_cache=False)


def ReceivePromptResult(id: str | None = None) -> Any:
    """Return a dependency descriptor for a receive prompt's send result."""

    def _receive_prompt_result(matcher: "Matcher") -> Any:
        return matcher.state.get(
            REJECT_PROMPT_RESULT_KEY.format(key=RECEIVE_KEY.format(id=id))
        )

    return Depends(_receive_prompt_result, use_cache=False)


def PausePromptResult() -> Any:
    """Return a dependency descriptor for a pause prompt's send result."""

    def _pause_prompt_result(matcher: "Matcher") -> Any:
        return matcher.state.get(PAUSE_PROMPT_RESULT_KEY)

    return Depends(_pause_prompt_result, use_cache=False)


__autodoc__ = {
    "Arg": True,
    "ArgStr": True,
    "Depends": True,
    "ArgParam": True,
    "ChannelParam": True,
    "EventParam": True,
    "StateParam": True,
    "DependParam": True,
    "ArgPlainText": True,
    "DefaultParam": True,
    "MatcherParam": True,
    "ExceptionParam": True,
    "ArgPromptResult": True,
}
