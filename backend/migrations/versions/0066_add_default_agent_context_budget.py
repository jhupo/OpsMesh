"""add default agent context budget

Revision ID: 0066_agent_context_budget
Revises: 0065_file_runtime_policy
Create Date: 2026-09-09 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066_agent_context_budget"
down_revision: str | None = "0065_file_runtime_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_CONTEXT_BUDGET = {
    "max_input_tokens": None,
    "context_window_tokens": None,
    "output_reserve_tokens": 4_096,
    "safety_margin_tokens": 1_024,
}


def upgrade() -> None:
    _rewrite_context_policy(add_default=True)


def downgrade() -> None:
    _rewrite_context_policy(add_default=False)


def _rewrite_context_policy(*, add_default: bool) -> None:
    profiles = sa.table(
        "agent_profiles",
        sa.column("id", sa.Uuid()),
        sa.column("memory_policy", sa.JSON()),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.select(profiles.c.id, profiles.c.memory_policy)).all()
    for profile_id, raw_policy in rows:
        policy = dict(raw_policy) if isinstance(raw_policy, dict) else {}
        if add_default:
            context = policy.get("context_budget")
            policy["context_budget"] = {
                **_DEFAULT_CONTEXT_BUDGET,
                **(context if isinstance(context, dict) else {}),
            }
        else:
            policy.pop("context_budget", None)
        bind.execute(
            profiles.update().where(profiles.c.id == profile_id).values(memory_policy=policy)
        )
