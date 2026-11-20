from __future__ import annotations
from collections.abc import Iterable
import json
from dataclasses import dataclass

def encode_scope(*parts: str) -> str:
    """Encode identifier parts without delimiter ambiguity."""
    return json.dumps(parts, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class UserContext:
    """A protocol-provided profile, not a verified principal."""

    id: str
    name: str = ""
    groups: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()

    @classmethod
    def scoped(
        cls,
        connection_id: str,
        user_id: str,
        *,
        name: str = "",
        groups: Iterable[str] = (),
        roles: Iterable[str] = (),
    ) -> UserContext:
        """Key a native user identifier to the connection that named it."""
        return cls(
            id=encode_scope(user_id, connection_id),
            name=name,
            groups=frozenset(groups),
            roles=frozenset(roles),
        )

    def __post_init__(self) -> None:
        """Validate profile fields and freeze user-owned collections."""
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("User id must be a nonempty string")
        if not isinstance(self.name, str):
            raise TypeError("User name must be a string")
        for field in ("groups", "roles"):
            values = getattr(self, field)
            if isinstance(values, str):
                raise TypeError(f"User {field} must be a collection")
            frozen = frozenset(values)
            if any(not isinstance(value, str) or not value for value in frozen):
                raise ValueError(f"User {field} must contain nonempty strings")
            object.__setattr__(self, field, frozen)
