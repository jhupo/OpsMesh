from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.typing import dict_or_empty, int_or_zero, string_list
from backend.app.workspaces.data_lifecycle import WorkspaceDataLifecycleService


class WorkspaceDataLifecycleRollupService:
    def __init__(self, session: Session) -> None:
        self._data_lifecycle = WorkspaceDataLifecycleService(session)

    def data_lifecycle_rollup(self, workspace_id: UUID) -> dict[str, object]:
        readiness = self._data_lifecycle.get_recovery_readiness(workspace_id=workspace_id)
        if readiness is None:
            return unknown_data_lifecycle_rollup()
        restore_readiness = dict_or_empty(readiness.get("restore_readiness"))
        retention_safety = dict_or_empty(readiness.get("retention_safety"))
        latest_backup = dict_or_empty(readiness.get("latest_successful_archive_export"))
        latest_restore_drill = dict_or_empty(readiness.get("latest_restore_drill"))
        import_conflict_history = dict_or_empty(restore_readiness.get("import_conflict_history"))
        blocked_reasons = string_list(restore_readiness.get("blocked_reasons"))
        warnings = string_list(restore_readiness.get("warnings"))
        recommended_actions = string_list(restore_readiness.get("recommended_actions"))
        return {
            "status": data_lifecycle_status(
                ready=restore_readiness.get("ready") is True,
                blocked_reasons=blocked_reasons,
                warnings=warnings,
            ),
            "ready": restore_readiness.get("ready") is True,
            "blocked_reasons": blocked_reasons,
            "warnings": warnings,
            "recommended_actions": recommended_actions,
            "next_safe_action": recommended_actions[0] if recommended_actions else None,
            "latest_backup": latest_backup_rollup(latest_backup),
            "latest_restore_drill": latest_restore_drill_rollup(latest_restore_drill),
            "retention_safety": retention_safety_rollup(retention_safety),
            "import_conflict_preview": import_conflict_preview_rollup(import_conflict_history),
        }


def unknown_data_lifecycle_rollup() -> dict[str, object]:
    return {
        "status": "unknown",
        "ready": False,
        "blocked_reasons": ["workspace_not_found"],
        "warnings": [],
        "recommended_actions": [],
    }


def latest_backup_rollup(latest_backup: dict[str, object]) -> dict[str, object]:
    return {
        "job_id": string_or_none(latest_backup.get("id")),
        "status": latest_backup.get("status"),
        "completed_at": iso_datetime_or_none(latest_backup.get("completed_at")),
        "storage_object_configured": latest_backup.get("has_storage_object") is True,
        "checksum_configured": latest_backup.get("checksum_sha256") is not None,
        "size_bytes": latest_backup.get("size_bytes"),
    }


def latest_restore_drill_rollup(latest_restore_drill: dict[str, object]) -> dict[str, object]:
    return {
        "event_id": string_or_none(latest_restore_drill.get("id")),
        "created_at": iso_datetime_or_none(latest_restore_drill.get("created_at")),
        "action": latest_restore_drill.get("action"),
    }


def retention_safety_rollup(retention_safety: dict[str, object]) -> dict[str, object]:
    return {
        "retention_enabled": retention_safety.get("retention_enabled") is True,
        "backup_policy_enabled": retention_safety.get("backup_policy_enabled") is True,
        "protected_by_successful_archive": (
            retention_safety.get("protected_by_successful_archive") is True
        ),
        "warnings": string_list(retention_safety.get("warnings")),
    }


def import_conflict_preview_rollup(
    import_conflict_history: dict[str, object],
) -> dict[str, object]:
    return {
        "preview_count": int_or_zero(import_conflict_history.get("total_previews")),
        "required_resolution_count": int_or_zero(
            import_conflict_history.get("required_resolution_count")
        ),
        "suggested_resolution_count": int_or_zero(
            import_conflict_history.get("suggested_resolution_count")
        ),
        "latest_preview_at": iso_datetime_or_none(
            import_conflict_history.get("latest_previewed_at")
        ),
    }


def string_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def iso_datetime_or_none(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def data_lifecycle_status(
    *,
    ready: bool,
    blocked_reasons: list[str],
    warnings: list[str],
) -> str:
    if ready:
        return "ready_with_warnings" if warnings else "ready"
    if blocked_reasons:
        return "blocked"
    return "unknown"
