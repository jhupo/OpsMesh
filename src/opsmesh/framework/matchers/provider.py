"""Storage providers for matcher registration."""

import abc
from collections import defaultdict
from collections.abc import Mapping, MutableMapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .matcher import Matcher


class MatcherProvider(abc.ABC, MutableMapping[int, list[type["Matcher"]]]):
    """Base interface for matcher storage providers.

    Args:
        matchers: Matchers already stored in the current provider.
    """

    @abc.abstractmethod
    def __init__(self, matchers: Mapping[int, list[type["Matcher"]]]):
        raise NotImplementedError


class _DictProvider(defaultdict[int, list[type["Matcher"]]], MatcherProvider):  # type: ignore
    def __init__(self, matchers: Mapping[int, list[type["Matcher"]]]):
        super().__init__(list, matchers)


DEFAULT_PROVIDER_CLASS = _DictProvider
"""The default storage provider class."""
