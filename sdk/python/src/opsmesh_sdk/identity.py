from dataclasses import dataclass


@dataclass(frozen=True)
class UserIdentity:
    user_id: str
    roles: frozenset[str] = frozenset()
    group_ids: frozenset[str] = frozenset()

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles
