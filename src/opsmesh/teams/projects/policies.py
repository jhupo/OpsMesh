from __future__ import annotations

ACTIVE_RESERVATION_STATUSES = {"active", "reserved"}

SPACE_TIER_TEMPLATES: list[dict[str, object]] = [
    {
        "tier": "small",
        "recommended_for": "single_project_pilot",
        "quota_limits": {"active_runs": 2, "storage_mb": 5_120, "memory_entries": 1_000},
    },
    {
        "tier": "standard",
        "recommended_for": "department_delivery_team",
        "quota_limits": {"active_runs": 8, "storage_mb": 25_600, "memory_entries": 10_000},
    },
    {
        "tier": "large",
        "recommended_for": "multi_project_program",
        "quota_limits": {"active_runs": 24, "storage_mb": 102_400, "memory_entries": 50_000},
    },
    {
        "tier": "enterprise",
        "recommended_for": "company_wide_portfolio",
        "quota_limits": {"active_runs": 64, "storage_mb": 1_048_576, "memory_entries": 250_000},
    },
]
