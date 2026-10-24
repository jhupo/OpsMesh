"""Frozen migration tables that have no current application owner.

Keep this explicit: ignoring every reflected-only table would hide schema drift.
Retirement here does not authorize dropping historical user data.
"""

RETIRED_TABLES = frozenset(
    {
        "gateway_senders",
        "gateway_subjects",
        "gateway_routes",
        "gateway_route_receipts",
        "gateway_admission_history",
        "agent_definitions",
        "agent_grants",
        "agent_invitations",
        "agent_revisions",
        "capability_definitions",
        "capability_packages",
        "capability_publication_reviews",
        "execution_mcp_uses",
        "execution_approvals",
        "execution_signals",
        "http_creation_receipts",
        "mcp_connections",
        "model_providers",
        "provider_models",
    }
)


def include_schema_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Exclude only known retired tables from autogenerate/model comparison."""
    return not (type_ == "table" and reflected and compare_to is None and name in RETIRED_TABLES)
