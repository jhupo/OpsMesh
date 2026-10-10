"""add default episodic memory policy

Revision ID: 0068_episodic_memory
Revises: 0067_memory_layers
Create Date: 2026-09-09 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0068_episodic_memory"
down_revision: str | None = "0067_memory_layers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_EPISODIC_MEMORY = {
    "capture_enabled": True,
    "retrieval_enabled": True,
    "retention_days": 180,
    "max_results": 8,
    "default_importance": 30,
}
_DEFAULT_CONTEXT_BUDGET = {
    "max_input_tokens": None,
    "context_window_tokens": None,
    "output_reserve_tokens": 4_096,
    "safety_margin_tokens": 1_024,
}
_DEFAULT_WORKING_MEMORY = {
    "enabled": True,
    "ttl_seconds": 86_400,
    "max_entries": 64,
    "max_entry_tokens": 2_048,
}


def upgrade() -> None:
    _rewrite_policy(add_episodic=True)


def downgrade() -> None:
    _rewrite_policy(add_episodic=False)


def _rewrite_policy(*, add_episodic: bool) -> None:
    profiles = sa.table(
        "agent_profiles",
        sa.column("id", sa.Uuid()),
        sa.column("memory_policy", sa.JSON()),
    )
    bind = op.get_bind()
    for profile_id, raw_policy in bind.execute(
        sa.select(profiles.c.id, profiles.c.memory_policy)
    ).all():
        policy = _normalized_policy(raw_policy, add_episodic=add_episodic)
        bind.execute(
            profiles.update().where(profiles.c.id == profile_id).values(memory_policy=policy)
        )
    _rewrite_listing_snapshots(bind, add_episodic=add_episodic)


def _rewrite_listing_snapshots(bind: sa.Connection, *, add_episodic: bool) -> None:
    listings = sa.table(
        "talent_listings",
        sa.column("id", sa.Uuid()),
        sa.column("metadata", sa.JSON()),
    )
    for listing_id, raw_metadata in bind.execute(
        sa.select(listings.c.id, listings.c.metadata)
    ).all():
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        raw_snapshot = metadata.get("agent_snapshot")
        if not isinstance(raw_snapshot, dict):
            continue
        snapshot = dict(raw_snapshot)
        snapshot["memory_policy"] = _normalized_policy(
            snapshot.get("memory_policy"),
            add_episodic=add_episodic,
        )
        metadata["agent_snapshot"] = snapshot
        bind.execute(
            listings.update().where(listings.c.id == listing_id).values(metadata=metadata)
        )


def _normalized_policy(raw_policy: object, *, add_episodic: bool) -> dict[str, object]:
    source = dict(raw_policy) if isinstance(raw_policy, dict) else {}
    context = source.get("context_budget")
    working = source.get("working_memory")
    policy: dict[str, object] = {
        "context_budget": _merged_section(_DEFAULT_CONTEXT_BUDGET, context),
        "working_memory": _merged_section(_DEFAULT_WORKING_MEMORY, working),
    }
    if add_episodic:
        policy["episodic_memory"] = dict(_DEFAULT_EPISODIC_MEMORY)
    return policy


def _merged_section(defaults: dict[str, object], value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    return {key: source.get(key, default) for key, default in defaults.items()}
