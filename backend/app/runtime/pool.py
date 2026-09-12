from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PoolPolicy:
    """Capacity and reset policy for reusable runtime members."""

    max_members: int = 8
    lease_timeout_seconds: int = 900
    reset_before_reuse: bool = True

    def __post_init__(self) -> None:
        if self.max_members < 1 or self.lease_timeout_seconds < 1:
            raise ValueError("Runtime pool limits must be positive")


def select_available_member(member_ids: Iterable[str], leased_ids: set[str]) -> str | None:
    """Select the first healthy unleased pool member deterministically."""
    return next((member_id for member_id in member_ids if member_id not in leased_ids), None)
