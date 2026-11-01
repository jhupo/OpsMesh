"""Dependency declarations and matcher argument providers."""

from __future__ import annotations

from typing import Any

from .adapters import OpsMeshMessage
from .dependencies import Depends


def _command_arg() -> OpsMeshMessage:
    from .matcher import get_current_matcher

    value = get_current_matcher().state.get("command_arg", OpsMeshMessage())
    if not isinstance(value, OpsMeshMessage):
        raise TypeError("Command argument is not a OpsMeshMessage")
    return value


def CommandArg() -> Any:
    return Depends(_command_arg, use_cache=False)


def Arg(key: str) -> Any:
    def get_argument() -> OpsMeshMessage:
        from .matcher import get_current_matcher

        value = get_current_matcher().state[key]
        return value if isinstance(value, OpsMeshMessage) else OpsMeshMessage(str(value))

    return Depends(get_argument, use_cache=False)


def ArgPlainText(key: str) -> Any:
    def get_text() -> str:
        from .matcher import get_current_matcher

        value = get_current_matcher().state[key]
        return value.extract_plain_text() if isinstance(value, OpsMeshMessage) else str(value)

    return Depends(get_text, use_cache=False)


__all__ = ["Arg", "ArgPlainText", "CommandArg", "Depends"]
