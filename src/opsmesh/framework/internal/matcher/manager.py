"""Process-local matcher index; persistence belongs to business workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .matcher import OpsMeshMatcher


class MatcherManager:
    def __init__(self) -> None:
        self._matchers: list[OpsMeshMatcher] = []

    def add(self, matcher: OpsMeshMatcher) -> None:
        self._matchers.append(matcher)

    def remove(self, matcher: OpsMeshMatcher) -> None:
        if matcher in self._matchers:
            self._matchers.remove(matcher)

    def snapshot(self) -> tuple[OpsMeshMatcher, ...]:
        return tuple(self._matchers)

    def clear(self) -> None:
        self._matchers.clear()


matchers = MatcherManager()
