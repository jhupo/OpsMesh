"""Remove settings of the retired recursive Agent tool executor."""

from alembic import op

revision = "0131_retired_agent_tool_settings"
down_revision = "0130_retired_runtime_network_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE platform_policies SET value = value - ARRAY["
        "'agent_tool_depth', 'agent_tool_turns', 'agent_tool_targets', 'agent_tool_graph_nodes'] "
        "WHERE policy_key = 'operational_configuration'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE platform_policies SET value = "
        "jsonb_build_object('agent_tool_depth',3,'agent_tool_turns',20,"
        "'agent_tool_targets',10,'agent_tool_graph_nodes',25) || value "
        "WHERE policy_key = 'operational_configuration'"
    )
