"""Trusted user information shared by all channel protocols."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UserContext:
    """Carry verified identity only, without credentials or runtime services."""

    id: str
    name: str = ""
    groups: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Validate identity fields and freeze user-owned collections."""
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("User id must be a nonempty string")
        if not isinstance(self.name, str):
            raise TypeError("User name must be a string")
        object.__setattr__(self, "name", self.name or self.id)
        for field in ("groups", "roles"):
            values = getattr(self, field)
            if isinstance(values, str):
                raise TypeError(f"User {field} must be a collection")
            frozen = frozenset(values)
            if any(not isinstance(value, str) or not value for value in frozen):
                raise ValueError(f"User {field} must contain nonempty strings")
            object.__setattr__(self, field, frozen)
