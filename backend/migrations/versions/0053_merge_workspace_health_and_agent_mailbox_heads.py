"""Merge workspace health and agent mailbox migration heads."""

from collections.abc import Sequence

revision: str = "0053_merge_workspace_health_and_agent_mailbox_heads"
down_revision: tuple[str, str] = (
    "0037_workspace_health_snapshots",
    "0052_agent_team_mailbox",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
