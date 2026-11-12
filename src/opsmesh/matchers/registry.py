"""Own the process-local matcher registry independently of public exports."""

from .manager import MatcherManager

matchers = MatcherManager()
