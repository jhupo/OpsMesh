from datetime import UTC, datetime
from uuid import UUID

from backend.app.exports.models import WorkspaceExportJob
from backend.app.workspaces.data_lifecycle_schedule import _backup_schedule_status
from backend.app.workspaces.data_lifecycle_settings import (
    _backup_settings,
    _ensure_utc_datetime,
    _positive_int,
    _retention_settings,
)


def _retention_policy(settings: dict[str, object]) -> dict[str, object]:
    raw_policy = _retention_settings(settings)
    enabled = bool(raw_policy.get("enabled", False))
    warnings: list[str] = []
    if not enabled:
        warnings.append("retention_policy_not_enabled")
    return {
        "enabled": enabled,
        "source": "workspace.settings.data_lifecycle.retention",
        "default_retention_days": _positive_int(raw_policy.get("default_retention_days")),
        "file_retention_days": _positive_int(raw_policy.get("file_retention_days")),
        "export_job_retention_days": _positive_int(raw_policy.get("export_job_retention_days")),
        "artifact_retention_days": _positive_int(raw_policy.get("artifact_retention_days")),
        "audit_event_retention_days": _positive_int(raw_policy.get("audit_event_retention_days")),
        "delete_policy": str(raw_policy.get("delete_policy") or "manual_review"),
        "warnings": warnings,
    }


def _backup_policy(
    settings: dict[str, object],
    latest_job: WorkspaceExportJob | None,
    latest_success: WorkspaceExportJob | None,
    *,
    generated_at: datetime,
) -> dict[str, object]:
    raw_policy = _backup_settings(settings)
    enabled = bool(raw_policy.get("enabled", False))
    warnings: list[str] = []
    if not enabled:
        warnings.append("backup_policy_not_enabled")
    if latest_success is None:
        warnings.append("no_successful_archive_export")
    schedule_status = _backup_schedule_status(
        raw_policy=raw_policy,
        enabled=enabled,
        latest_success=latest_success,
        generated_at=generated_at,
    )
    warnings.extend(schedule_status["warnings"])
    return {
        "enabled": enabled,
        "source": "workspace.settings.data_lifecycle.backup",
        "schedule": raw_policy.get("schedule"),
        "target_type": raw_policy.get("target_type") or "manual_export",
        "target": raw_policy.get("target"),
        "max_archive_age_days": _positive_int(
            raw_policy.get("max_archive_age_days") or raw_policy.get("max_age_days")
        ),
        "last_export_job_status": latest_job.status if latest_job is not None else None,
        "last_successful_archive_export_at": (
            latest_success.completed_at if latest_success is not None else None
        ),
        "schedule_status": schedule_status,
        "warnings": warnings,
    }


def _readiness(
    retention_policy: dict[str, object],
    backup_policy: dict[str, object],
    latest_success: WorkspaceExportJob | None,
) -> dict[str, object]:
    blocked_reasons: list[str] = []
    if retention_policy["enabled"] is not True:
        blocked_reasons.append("retention_policy_not_enabled")
    if backup_policy["enabled"] is not True:
        blocked_reasons.append("backup_policy_not_enabled")
    if latest_success is None:
        blocked_reasons.append("no_successful_archive_export")
    return {
        "ready": not blocked_reasons,
        "blocked_reasons": blocked_reasons,
        "warnings": [*retention_policy["warnings"], *backup_policy["warnings"]],
    }


def _retention_days(policy: dict[str, object], key: str) -> int | None:
    value = policy.get(key) or policy.get("default_retention_days")
    return value if isinstance(value, int) and value > 0 else None


def _retention_blocked_reasons(
    *,
    policy: dict[str, object],
    require_successful_backup: bool,
    latest_success: WorkspaceExportJob | None,
    include_files: bool,
    include_export_jobs: bool,
    include_artifacts: bool,
) -> list[str]:
    reasons: list[str] = []
    if policy["enabled"] is not True:
        reasons.append("retention_policy_not_enabled")
    if require_successful_backup and latest_success is None:
        reasons.append("no_successful_archive_export")
    if not any([include_files, include_export_jobs, include_artifacts]):
        reasons.append("no_retention_targets_enabled")
    return reasons


def _retention_warnings(
    policy: dict[str, object],
    candidates: list[dict[str, object]],
) -> list[str]:
    warnings = list(policy["warnings"]) if isinstance(policy.get("warnings"), list) else []
    if any(candidate["action"] == "manual_review" for candidate in candidates):
        warnings.append("manual_review_candidates_present")
    return warnings


def _retention_recommended_actions(
    *,
    blocked_reasons: list[str],
    warnings: list[str],
    candidates: list[dict[str, object]],
    apply_changes: bool,
) -> list[str]:
    actions: list[str] = []
    reason_set = set(blocked_reasons)
    warning_set = set(warnings)
    if "retention_policy_not_enabled" in reason_set:
        actions.append("enable_retention_policy")
    if "no_successful_archive_export" in reason_set:
        actions.append("run_archive_export")
    if "no_retention_targets_enabled" in reason_set:
        actions.append("select_retention_targets")
    if "manual_review_candidates_present" in warning_set:
        actions.append("review_retention_candidates")
    if not blocked_reasons and candidates and not apply_changes:
        actions.append("apply_retention")
    if not blocked_reasons and not candidates:
        actions.append("no_retention_candidates")
    return actions


def _candidate_payload(
    *,
    resource_type: str,
    resource_id: UUID,
    created_at: datetime,
    generated_at: datetime,
    retention_days: int,
    status: str,
    action: str,
    filename: str | None,
    size_bytes: int | None,
    reason: str | None = None,
) -> dict[str, object]:
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "created_at": created_at,
        "age_days": _age_days(generated_at, created_at),
        "retention_days": retention_days,
        "status": status,
        "action": action,
        "filename": filename,
        "size_bytes": size_bytes,
        "reason": reason,
    }


def _age_days(now: datetime, then: datetime) -> int:
    normalized_then = _ensure_utc_datetime(then) or then.replace(tzinfo=UTC)
    return max((now - normalized_then).days, 0)


def _candidate_counts(candidates: list[dict[str, object]]) -> dict[str, int]:
    counts = {"files": 0, "export_jobs": 0, "artifacts": 0, "total": len(candidates)}
    for candidate in candidates:
        if candidate["resource_type"] == "file":
            counts["files"] += 1
        elif candidate["resource_type"] == "export_job":
            counts["export_jobs"] += 1
        elif candidate["resource_type"] == "artifact":
            counts["artifacts"] += 1
    return counts
