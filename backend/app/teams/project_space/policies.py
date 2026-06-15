from backend.app.orchestration.statuses import ORCHESTRATION_ACTIVE_RUN_STATUSES

ACTIVE_RUN_STATUSES = ORCHESTRATION_ACTIVE_RUN_STATUSES
ACTIVE_RESERVATION_STATUSES = {"active", "reserved"}

SPACE_TIER_TEMPLATES: list[dict[str, object]] = [
    {
        "tier": "small",
        "recommended_for": "single_project_pilot",
        "quota_limits": {"active_runs": 2, "storage_mb": 5_120, "memory_entries": 1_000},
        "cleanup_policy": {"completed_task_retention_days": 30, "artifact_archive_days": 60},
    },
    {
        "tier": "standard",
        "recommended_for": "department_delivery_team",
        "quota_limits": {"active_runs": 8, "storage_mb": 25_600, "memory_entries": 10_000},
        "cleanup_policy": {"completed_task_retention_days": 90, "artifact_archive_days": 180},
    },
    {
        "tier": "large",
        "recommended_for": "multi_project_program",
        "quota_limits": {"active_runs": 24, "storage_mb": 102_400, "memory_entries": 50_000},
        "cleanup_policy": {"completed_task_retention_days": 180, "artifact_archive_days": 365},
    },
    {
        "tier": "enterprise",
        "recommended_for": "company_wide_portfolio",
        "quota_limits": {"active_runs": 64, "storage_mb": 1_048_576, "memory_entries": 250_000},
        "cleanup_policy": {"completed_task_retention_days": 365, "artifact_archive_days": 730},
    },
]
