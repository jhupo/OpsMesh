"""Composable conditions evaluated with the same dependency scope as handlers."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .adapters import MessageEvent, OpsMeshEvent, OpsMeshMessage
from .dependencies import CallableDependency, DependencyScope


class Rule:
    def __init__(self, *checks: Callable[..., Any]) -> None:
        self.checks = tuple(CallableDependency.parse(check) for check in checks)

    async def __call__(self, scope: DependencyScope) -> bool:
        for check in self.checks:
            if not await scope.invoke(check):
                return False
        return True

    def __and__(self, other: Rule | Callable[..., Any]) -> Rule:
        return CompositeRule(self, as_rule(other), "and")

    def __or__(self, other: Rule | Callable[..., Any]) -> Rule:
        return CompositeRule(self, as_rule(other), "or")

    def __invert__(self) -> Rule:
        return CompositeRule(self, Rule(), "not")


class CompositeRule(Rule):
    def __init__(self, left: Rule, right: Rule, operation: str) -> None:
        self.left, self.right, self.operation = left, right, operation

    async def __call__(self, scope: DependencyScope) -> bool:
        left = await self.left(scope)
        if self.operation == "not":
            return not left
        if self.operation == "and":
            return left and await self.right(scope)
        return left or await self.right(scope)


def as_rule(value: Rule | Callable[..., Any] | None) -> Rule:
    if value is None:
        return Rule()
    return value if isinstance(value, Rule) else Rule(value)


def command(name: str, *, prefixes: tuple[str, ...] = ("/",)) -> Rule:
    if not name or any(c.isspace() for c in name):
        raise ValueError("Command name must be nonempty and contain no whitespace")

    def check(event: OpsMeshEvent, state: dict[str, Any]) -> bool:
        if not isinstance(event, MessageEvent):
            return False
        text = event.get_plaintext().lstrip()
        for prefix in prefixes:
            token = prefix + name
            if text == token or (
                text.startswith(token) and text[len(token) : len(token) + 1].isspace()
            ):
                state["command"] = name
                state["command_arg"] = OpsMeshMessage(text[len(token) :].lstrip())
                return True
        return False

    return Rule(check)


def regex(pattern: str, flags: int = 0) -> Rule:
    compiled = re.compile(pattern, flags)

    def check(event: OpsMeshEvent, state: dict[str, Any]) -> bool:
        if not isinstance(event, MessageEvent):
            return False
        match = compiled.search(event.get_plaintext())
        if match:
            state["regex_match"] = match
        return match is not None

    return Rule(check)


def to_me() -> Rule:
    def check(event: OpsMeshEvent) -> bool:
        return event.is_tome()

    return Rule(check)
