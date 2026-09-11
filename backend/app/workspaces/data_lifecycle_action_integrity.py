from uuid import UUID

from backend.app.api.services.exports import WorkspaceExportService
from backend.app.files.storage import ObjectStorage
from backend.app.observability.audit_service import AuditService
from backend.app.workspaces.data_lifecycle_payloads import _archive_integrity_payload
from backend.app.workspaces.data_lifecycle_recovery import (
    _recovery_action_result,
    _recovery_action_skipped,
)
from backend.app.workspaces.data_lifecycle_store import LifecycleStore
from backend.app.workspaces.models import Workspace


class RecoveryArchiveIntegrityActionMixin(LifecycleStore):
    def _apply_recovery_archive_integrity_action(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        storage: ObjectStorage | None,
        dry_run: bool,
        reason: str | None,
        metadata_keys: list[str],
        blocked_reasons: list[str],
        warnings: list[str],
    ) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        action = "verify_latest_archive_integrity"
        latest_success = self._repo.latest_successful_archive_export(workspace.id)
        if latest_success is None:
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace",
                resource_id=workspace.id,
                reason="no_successful_archive_export",
                blocked_reasons=blocked_reasons,
            )

        latest_integrity = self._repo.latest_archive_integrity_event(workspace.id)
        integrity_payload = _archive_integrity_payload(
            latest_integrity,
            latest_success=latest_success,
        )
        if (
            integrity_payload["latest_check_covers_latest_successful_archive"] is True
            and integrity_payload["latest_check_verified"] is True
        ):
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="archive_integrity_already_verified",
                blocked_reasons=blocked_reasons,
            )
        if storage is None:
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="storage_unavailable",
                blocked_reasons=blocked_reasons,
            )

        if dry_run:
            return _recovery_action_result(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                status="would_apply",
                blocked_reasons=blocked_reasons,
                metadata={
                    "readiness_warnings": warnings,
                    "latest_check_covers_latest_successful_archive": integrity_payload[
                        "latest_check_covers_latest_successful_archive"
                    ],
                },
            ), None

        try:
            verification = WorkspaceExportService(self._session).verify_archive_export_job(
                workspace_id=workspace.id,
                job_id=latest_success.id,
                user_id=user_id,
                storage=storage,
            )
        except (FileNotFoundError, ValueError):
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="archive_integrity_verification_failed",
                blocked_reasons=blocked_reasons,
            )

        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.recovery_readiness.archive_integrity_verified",
            target_type="workspace_export_job",
            target_id=latest_success.id,
            metadata={
                "reason": reason,
                "metadata_keys": metadata_keys,
                "readiness_blocked_reasons": blocked_reasons,
                "readiness_warnings": warnings,
                "verified": verification["verified"],
                "failed_checks": verification["failed_checks"],
            },
        )
        return _recovery_action_result(
            action=action,
            resource_type="workspace_export_job",
            resource_id=latest_success.id,
            status="applied",
            blocked_reasons=blocked_reasons,
            metadata={
                "verified": verification["verified"],
                "failed_checks": verification["failed_checks"],
                "checks": verification["checks"],
            },
        ), None
