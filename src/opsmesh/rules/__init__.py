"""Public rules with checks loaded after matcher initialization."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .base import Rule as Rule

if TYPE_CHECKING:
    from .checks import CMD_RESULT as CMD_RESULT
    from .checks import TRIE_VALUE as TRIE_VALUE
    from .checks import ArgumentParser as ArgumentParser
    from .checks import CommandRule as CommandRule
    from .checks import EndswithRule as EndswithRule
    from .checks import FullmatchRule as FullmatchRule
    from .checks import IsTypeRule as IsTypeRule
    from .checks import KeywordsRule as KeywordsRule
    from .checks import RegexRule as RegexRule
    from .checks import ShellCommandRule as ShellCommandRule
    from .checks import StartswithRule as StartswithRule
    from .checks import T as T
    from .checks import ToMeRule as ToMeRule
    from .checks import TrieRule as TrieRule
    from .checks import command as command
    from .checks import endswith as endswith
    from .checks import fullmatch as fullmatch
    from .checks import is_type as is_type
    from .checks import keyword as keyword
    from .checks import regex as regex
    from .checks import shell_command as shell_command
    from .checks import startswith as startswith
    from .checks import to_me as to_me

_CHECKS = (
    "ArgumentParser",
    "CMD_RESULT",
    "CommandRule",
    "EndswithRule",
    "FullmatchRule",
    "IsTypeRule",
    "KeywordsRule",
    "RegexRule",
    "ShellCommandRule",
    "StartswithRule",
    "T",
    "TRIE_VALUE",
    "ToMeRule",
    "TrieRule",
    "command",
    "endswith",
    "fullmatch",
    "is_type",
    "keyword",
    "regex",
    "shell_command",
    "startswith",
    "to_me",
)


__all__ = ("Rule", *_CHECKS)


def __getattr__(name: str) -> Any:
    """Defer checks that depend on matcher injection descriptors."""
    if name not in _CHECKS:
        raise AttributeError(f"Unknown rules export: {name}")
    value = getattr(import_module(".checks", __name__), name)
    globals()[name] = value
    return value
