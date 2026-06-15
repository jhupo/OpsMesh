from __future__ import annotations

from datetime import datetime
from uuid import UUID

from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.typing import dict_or_empty, int_or_zero, string_list
from backend.app.operations.models import WorkerHeartbeat
from backend.app.operations.observability import OperationsObservabilityService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.workspaces.data_lifecycle import WorkspaceDataLifecycleService


class OperationsOverviewService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None,
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder

    def overview_payload(self, workspace_id: UUID, queue_name: str) -> dict[str, object]:
        failed_runs = self._session.scalar(
            select(func.count()).select_from(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.status == "failed",
            )
        )
        offline_runtimes = self._session.scalar(
            select(func.count()).select_from(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.connection_status == "offline",
            )
        )
        workers_online = self._session.scalar(
            select(func.count()).select_from(WorkerHeartbeat).where(
                WorkerHeartbeat.workspace_id == workspace_id,
                WorkerHeartbeat.status == "online",
            )
        )
        recent_security_events = self._session.scalar(
            select(func.count()).select_from(SecurityEvent).where(
                SecurityEvent.workspace_id == workspace_id,
                SecurityEvent.severity.in_(["warning", "critical"]),
            )
        )
        return {
            "queue": OperationsObservabilityService(
                self._session,
                self._redis,
                self._keys,
            )
            .queue_metrics(queue_name, workspace_id)
            .model_dump(),
            "failed_runs": int(failed_runs or 0),
            "offline_runtimes": int(offline_runtimes or 0),
            "workers_online": int(workers_online or 0),
            "security_warnings": int(recent_security_events or 0),
            "data_lifecycle": self._data_lifecycle_rollup(workspace_id),
        }

    def _data_lifecycle_rollup(self, workspace_id: UUID) -> dict[str, object]:
        readiness = WorkspaceDataLifecycleService(self._session).get_recovery_readiness(
            workspace_id=workspace_id
        )
        if readiness is None:
            return {
                "status": "unknown",
                "ready": False,
                "blocked_reasons": ["workspace_not_found"],
                "warnings": [],
                "recommended_actions": [],
            }
        restore_readiness = dict_or_empty(readiness.get("restore_readiness"))
        retention_safety = dict_or_empty(readiness.get("retention_safety"))
        latest_backup = dict_or_empty(readiness.get("latest_successful_archive_export"))
        latest_restore_drill = dict_or_empty(readiness.get("latest_restore_drill"))
        import_conflict_history = dict_or_empty(
            restore_readiness.get("import_conflict_history")
        )
        blocked_reasons = string_list(restore_readiness.get("blocked_reasons"))
        warnings = string_list(restore_readiness.get("warnings"))
        recommended_actions = string_list(restore_readiness.get("recommended_actions"))
        return {
            "status": _data_lifecycle_status(
                ready=restore_readiness.get("ready") is True,
                blocked_reasons=blocked_reasons,
                warnings=warnings,
            ),
            "ready": restore_readiness.get("ready") is True,
            "blocked_reasons": blocked_reasons,
            "warnings": warnings,
            "recommended_actions": recommended_actions,
            "next_safe_action": recommended_actions[0] if recommended_actions else None,
            "latest_backup": {
                "job_id": _string_or_none(latest_backup.get("id")),
                "status": latest_backup.get("status"),
                "completed_at": _iso_datetime_or_none(latest_backup.get("completed_at")),
                "storage_object_configured": latest_backup.get("has_storage_object") is True,
                "checksum_configured": latest_backup.get("checksum_sha256") is not None,
                "size_bytes": latest_backup.get("size_bytes"),
            },
            "latest_restore_drill": {
                "event_id": _string_or_none(latest_restore_drill.get("id")),
                "created_at": _iso_datetime_or_none(latest_restore_drill.get("created_at")),
                "action": latest_restore_drill.get("action"),
            },
            "retention_safety": {
                "retention_enabled": retention_safety.get("retention_enabled") is True,
                "backup_policy_enabled": retention_safety.get("backup_policy_enabled") is True,
                "protected_by_successful_archive": (
                    retention_safety.get("protected_by_successful_archive") is True
                ),
                "warnings": string_list(retention_safety.get("warnings")),
            },
            "import_conflict_preview": {
                "preview_count": int_or_zero(import_conflict_history.get("total_previews")),
                "required_resolution_count": int_or_zero(
                    import_conflict_history.get("required_resolution_count")
                ),
                "suggested_resolution_count": int_or_zero(
                    import_conflict_history.get("suggested_resolution_count")
                ),
                "latest_preview_at": _iso_datetime_or_none(
                    import_conflict_history.get("latest_previewed_at")
                ),
            },
        }


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _iso_datetime_or_none(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _data_lifecycle_status(
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
