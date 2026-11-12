"""Store matcher classes by priority through a pluggable provider."""

from collections.abc import (
    ItemsView,
    Iterator,
    KeysView,
    MutableMapping,
    ValuesView,
)
from typing import TYPE_CHECKING, TypeVar, overload

from .provider import DEFAULT_PROVIDER_CLASS, MatcherProvider

if TYPE_CHECKING:
    from .matcher import Matcher

T = TypeVar("T")


class MatcherManager(MutableMapping[int, list[type["Matcher"]]]):
    """A priority-indexed registry of matcher classes.

    Exposes mapping operations over the configured matcher provider.
    """

    def __init__(self):
        self.provider: MatcherProvider = DEFAULT_PROVIDER_CLASS({})

    def __repr__(self) -> str:
        return f"MatcherManager(provider={self.provider!r})"

    def __contains__(self, o: object) -> bool:
        return o in self.provider

    def __iter__(self) -> Iterator[int]:
        return iter(self.provider)

    def __len__(self) -> int:
        return len(self.provider)

    def __getitem__(self, key: int) -> list[type["Matcher"]]:
        return self.provider[key]

    def __setitem__(self, key: int, value: list[type["Matcher"]]) -> None:
        self.provider[key] = value

    def __delitem__(self, key: int) -> None:
        del self.provider[key]

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, MatcherManager)
            and self.provider == other.provider
        )

    def keys(self) -> KeysView[int]:
        """Return registered matcher priorities."""
        return self.provider.keys()

    def values(self) -> ValuesView[list[type["Matcher"]]]:
        """Return the matcher lists stored at each priority."""
        return self.provider.values()

    def items(self) -> ItemsView[int, list[type["Matcher"]]]:
        """Return priority keys paired with their matcher lists."""
        return self.provider.items()

    @overload
    def get(self, key: int) -> list[type["Matcher"]] | None: ...

    @overload
    def get(
        self, key: int, default: list[type["Matcher"]]
    ) -> list[type["Matcher"]]: ...

    @overload
    def get(self, key: int, default: T) -> list[type["Matcher"]] | T: ...

    def get(
        self, key: int, default: T | None = None
    ) -> list[type["Matcher"]] | T | None:
        """Return matchers at a priority, or the supplied default."""
        return self.provider.get(key, default)

    def pop(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, key: int
    ) -> list[type["Matcher"]]:
        """Remove a priority and return its registered matchers."""
        return self.provider.pop(key)

    def popitem(self) -> tuple[int, list[type["Matcher"]]]:
        """Remove and return one priority entry from the provider."""
        return self.provider.popitem()

    def clear(self) -> None:
        """Remove all registered matchers from the provider."""
        self.provider.clear()

    def update(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, m: MutableMapping[int, list[type["Matcher"]]], /
    ) -> None:
        """Merge priority entries into the active provider."""
        self.provider.update(m)

    def setdefault(
        self, key: int, default: list[type["Matcher"]]
    ) -> list[type["Matcher"]]:
        """Return a priority entry, storing the default if absent."""
        return self.provider.setdefault(key, default)

    def set_provider(self, provider_class: type[MatcherProvider]) -> None:
        """Replace the matcher storage provider.

        Args:
            provider_class: The matcher storage provider class.
        """
        self.provider = provider_class(self.provider)
