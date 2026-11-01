"""OpsMeshMatcher control flow and execution-context errors."""

from __future__ import annotations


class MatcherControl(Exception):
    """Deliberate matcher control flow, not a system failure."""


class MatcherFinished(MatcherControl):
    pass


class MatcherPaused(MatcherControl):
    pass


class MatcherRejected(MatcherControl):
    pass


class MatcherStopPropagation(MatcherControl):
    pass


class MatcherSkipped(MatcherControl):
    pass


class ContextUnavailableError(RuntimeError):
    pass


class ReplyUnavailableError(ContextUnavailableError):
    pass


class DependencyError(ValueError):
    pass


class PluginError(RuntimeError):
    pass
