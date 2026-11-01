"""NoneBot-style declaration helpers with explicit plugin ownership."""

from __future__ import annotations

from typing import Any

from opsmesh.framework.adapters import MessageEvent, OpsMeshEvent
from opsmesh.framework.matcher import OpsMeshMatcher, matchers
from opsmesh.framework.rule import as_rule, command, regex

from .manager import get_current_plugin


def on(type: str | type[OpsMeshEvent] | None = None, **kwargs: Any) -> OpsMeshMatcher:
    owner = get_current_plugin()
    matcher = OpsMeshMatcher(event_type=type, owner=owner, **kwargs)
    matchers.add(matcher)
    if owner:
        owner.own(lambda: matchers.remove(matcher))
    return matcher


def on_event(event_type: type[OpsMeshEvent] | str, **kwargs: Any) -> OpsMeshMatcher:
    return on(event_type, **kwargs)


def on_message(**kwargs: Any) -> OpsMeshMatcher:
    return on(MessageEvent, **kwargs)


def on_command(name: str, *, prefixes: tuple[str, ...] = ("/",), **kwargs: Any) -> OpsMeshMatcher:
    rule = command(name, prefixes=prefixes) & as_rule(kwargs.pop("rule", None))
    return on(MessageEvent, rule=rule, **kwargs)


def on_regex(pattern: str, flags: int = 0, **kwargs: Any) -> OpsMeshMatcher:
    rule = regex(pattern, flags) & as_rule(kwargs.pop("rule", None))
    return on(MessageEvent, rule=rule, **kwargs)
