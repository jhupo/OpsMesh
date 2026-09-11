from datetime import datetime
from uuid import UUID

from backend.app.projects.export_models import WorkspaceExportJob
from backend.app.projects.export_status import WorkspaceExportJobStatus
from backend.app.workspaces.data_lifecycle_constants import RECOVERY_READINESS_APPLY_ACTIONS
from backend.app.workspaces.data_lifecycle_policy import _age_days
from backend.app.workspaces.data_lifecycle_settings import _safe_int, _string_list, _unique_strings


def _restore_readiness(
    *,
    latest_success: WorkspaceExportJob | None,
    backup_policy: dict[str, object],
    generated_at: datetime,
    active_job_count: object,
    backup_coverage: dict[str, object],
    restore_test_history: dict[str, object],
    import_conflict_history: dict[str, object],
    archive_integrity: dict[str, object],
) -> dict[str, object]:
    blocked_reasons: list[str] = []
    warnings: list[str] = []
    latest_archive_age_days = (
        _age_days(generated_at, latest_success.completed_at)
        if latest_success is not None and latest_success.completed_at is not None
        else None
    )
    max_archive_age_days = backup_policy.get("max_archive_age_days")
    if backup_policy["enabled"] is not True:
        blocked_reasons.append("backup_policy_not_enabled")
    if latest_success is None:
        blocked_reasons.append("no_successful_archive_export")
    else:
        if latest_success.storage_key is None:
            blocked_reasons.append("latest_archive_missing_storage_object")
        if latest_success.checksum_sha256 is None:
            blocked_reasons.append("latest_archive_missing_checksum")
        if latest_success.size_bytes is None or latest_success.size_bytes <= 0:
            blocked_reasons.append("latest_archive_empty_or_unknown_size")
        if (
            isinstance(max_archive_age_days, int)
            and latest_archive_age_days is not None
            and latest_archive_age_days > max_archive_age_days
        ):
            blocked_reasons.append("latest_archive_stale")
        latest_check = archive_integrity.get("latest_check")
        latest_check_verified = archive_integrity.get("latest_check_verified")
        latest_check_covers_archive = (
            archive_integrity.get("latest_check_covers_latest_successful_archive") is True
        )
        if latest_check_covers_archive and latest_check_verified is not True:
            blocked_reasons.append("latest_archive_integrity_check_failed")
        elif latest_check is not None and not latest_check_covers_archive:
            warnings.append("latest_archive_integrity_check_stale")
    latest_tested_at = restore_test_history.get("latest_tested_at")
    if not isinstance(latest_tested_at, datetime):
        blocked_reasons.append("no_archive_import_test_recorded")
    elif (
        latest_success is not None
        and latest_success.completed_at is not None
        and latest_tested_at < latest_success.completed_at
    ):
        blocked_reasons.append("restore_test_older_than_latest_archive")
    if backup_coverage.get("status") == "partial":
        blocked_reasons.append("backup_coverage_incomplete")
    if isinstance(active_job_count, int) and active_job_count > 0:
        warnings.append("archive_export_jobs_in_progress")
    if backup_coverage.get("status") == "unknown":
        warnings.append("backup_coverage_unknown")
    if _safe_int(import_conflict_history.get("required_resolution_count")) > 0:
        warnings.append("import_previews_have_required_resolutions")
    if "backup_schedule_overdue" in _string_list(backup_policy.get("warnings")):
        warnings.append("backup_schedule_overdue")
    return {
        "ready": not blocked_reasons,
        "blocked_reasons": blocked_reasons,
        "warnings": warnings,
        "backup_coverage": backup_coverage,
        "restore_test_history": restore_test_history,
        "import_conflict_history": import_conflict_history,
        "downloadable_archive_available": (
            latest_success is not None
            and latest_success.status == WorkspaceExportJobStatus.COMPLETED.value
            and latest_success.storage_key is not None
        ),
        "latest_archive_age_days": latest_archive_age_days,
        "max_archive_age_days": max_archive_age_days,
        "latest_archive_import_test_recorded": latest_tested_at is not None,
        "latest_archive_import_tested_at": latest_tested_at,
        "recommended_actions": _restore_recommended_actions(
            blocked_reasons,
            warnings=warnings,
        ),
    }


def _backup_coverage(
    *,
    latest_success: WorkspaceExportJob | None,
    current_counts: dict[str, int],
) -> dict[str, object]:
    if latest_success is None:
        return {
            "status": "missing",
            "score": 0,
            "current_counts": current_counts,
            "archived_counts": {},
            "uncovered_counts": current_counts,
            "total_current_resources": sum(current_counts.values()),
            "total_archived_resources": 0,
            "uncovered_resource_count": sum(current_counts.values()),
        }

    archived_counts = _manifest_counts(latest_success.job_metadata)
    if not archived_counts:
        return {
            "status": "unknown",
            "score": 50,
            "current_counts": current_counts,
            "archived_counts": {},
            "uncovered_counts": {},
            "total_current_resources": sum(current_counts.values()),
            "total_archived_resources": 0,
            "uncovered_resource_count": None,
        }

    uncovered_counts = {
        collection: max(current_count - int(archived_counts.get(collection, 0)), 0)
        for collection, current_count in current_counts.items()
    }
    total_current = sum(current_counts.values())
    total_uncovered = sum(uncovered_counts.values())
    score = (
        100
        if total_current == 0
        else int(((total_current - total_uncovered) / total_current) * 100)
    )
    return {
        "status": "verified" if total_uncovered == 0 else "partial",
        "score": max(min(score, 100), 0),
        "current_counts": current_counts,
        "archived_counts": {
            collection: int(archived_counts.get(collection, 0))
            for collection in current_counts
        },
        "uncovered_counts": {
            collection: count for collection, count in uncovered_counts.items() if count > 0
        },
        "total_current_resources": total_current,
        "total_archived_resources": sum(
            int(archived_counts.get(collection, 0)) for collection in current_counts
        ),
        "uncovered_resource_count": total_uncovered,
    }


def _manifest_counts(metadata: dict[str, object]) -> dict[str, int]:
    for key in ("manifest_counts", "counts"):
        raw_counts = metadata.get(key)
        if isinstance(raw_counts, dict):
            return {
                str(collection): int(count)
                for collection, count in raw_counts.items()
                if isinstance(count, int) and count >= 0
            }
    return {}


def _restore_recommended_actions(
    blocked_reasons: list[str],
    *,
    warnings: list[str] | None = None,
) -> list[str]:
    actions: list[str] = []
    warning_set = set(warnings or [])
    if "backup_policy_not_enabled" in blocked_reasons:
        actions.append("enable_backup_policy")
    if (
        "no_successful_archive_export" in blocked_reasons
        or "latest_archive_stale" in blocked_reasons
        or "backup_coverage_incomplete" in blocked_reasons
    ):
        actions.append("run_archive_export")
    if "backup_coverage_unknown" in warning_set:
        actions.append("run_archive_export_with_manifest_counts")
    if "backup_schedule_overdue" in warning_set and "run_archive_export" not in actions:
        actions.append("run_archive_export")
    if any(
        reason in blocked_reasons
        for reason in {
            "latest_archive_missing_storage_object",
            "latest_archive_missing_checksum",
            "latest_archive_empty_or_unknown_size",
            "latest_archive_integrity_check_failed",
        }
    ):
        actions.append("repair_or_regenerate_archive_export")
    if "latest_archive_integrity_check_stale" in warning_set:
        actions.append("verify_latest_archive_integrity")
    if "no_archive_import_test_recorded" in blocked_reasons:
        actions.append("run_restore_import_test")
    if "restore_test_older_than_latest_archive" in blocked_reasons:
        actions.append("run_restore_import_test")
    if "import_previews_have_required_resolutions" in warning_set:
        actions.append("resolve_import_conflicts_before_restore")
    return actions


def _recovery_readiness_actions(
    actions: list[str] | None,
    *,
    recommended_actions: list[str],
) -> list[str]:
    if not actions:
        if "run_archive_export" in recommended_actions:
            return ["run_archive_export"]
        return _unique_strings(
            action
            for action in recommended_actions
            if action in RECOVERY_READINESS_APPLY_ACTIONS
        )
    return _unique_strings(actions)


def _recovery_action_result(
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
    status: str,
    blocked_reasons: list[str],
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": status,
        "blocked_reasons": blocked_reasons,
        "metadata": metadata or {},
    }


def _recovery_action_skipped(
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
    reason: str,
    blocked_reasons: list[str],
) -> dict[str, object]:
    return {
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": "skipped",
        "reason": reason,
        "blocked_reasons": blocked_reasons,
    }
