from __future__ import annotations

from collections.abc import Iterable


def select_available_member(member_ids: Iterable[str], leased_ids: set[str]) -> str | None:
    """Select the first healthy unleased pool member deterministically."""
    return next((member_id for member_id in member_ids if member_id not in leased_ids), None)
