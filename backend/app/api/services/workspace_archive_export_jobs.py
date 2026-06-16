from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceArchiveExportRequest,
    WorkspaceArchiveImportRequest,
    WorkspaceArchiveRestoreDrillRequest,
)
from backend.app.api.services.workspace_archive_export_builder import WorkspaceArchiveExportBuilder
from backend.app.api.services.workspace_export_constants import SUPPORTED_WORKSPACE_EXPORT_FORMAT
from backend.app.api.services.workspace_import_preview import _import_preview_audit_metadata
from backend.app.audit.service import AuditService
from backend.app.exports.models import WorkspaceExportJob
from backend.app.exports.status import WorkspaceExportJobStatus
from backend.app.files.storage import ObjectStorage
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.models import Workspace


class WorkspaceArchiveExportJobService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_archive_export_job(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceArchiveExportRequest,
        queue: RedisQueue,
    ) -> WorkspaceExportJob:
        export_job = WorkspaceExportJob(
            workspace_id=workspace.id,
            created_by_user_id=user_id,
            export_type="workspace_archive",
            status=WorkspaceExportJobStatus.QUEUED.value,
            request=request.model_dump(mode="json"),
            job_metadata={},
        )
        self._session.add(export_job)
        self._session.flush()
        enqueued = queue.enqueue(
            JobPayload(
                workspace_id=workspace.id,
                job_type=JobType.WORKSPACE_ARCHIVE_EXPORT,
                resource_id=export_job.id,
                requested_by_user_id=user_id,
                idempotency_key=f"workspace.archive_export:{workspace.id}:{export_job.id}",
                max_attempts=2,
            )
        )
        if not enqueued:
            export_job.status = WorkspaceExportJobStatus.FAILED.value
            export_job.error = "Failed to enqueue archive export job"
            export_job.completed_at = datetime.now(UTC)
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.archive_export_job.created",
            target_type="workspace_export_job",
            target_id=export_job.id,
            metadata={"enqueued": enqueued},
        )
        self._session.commit()
        self._session.refresh(export_job)
        return export_job

    def get_export_job(self, *, workspace_id: UUID, job_id: UUID) -> WorkspaceExportJob | None:
        return self._session.scalar(
            select(WorkspaceExportJob).where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.id == job_id,
            )
        )

    def read_export_job_content(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        storage: ObjectStorage,
    ) -> tuple[WorkspaceExportJob, bytes]:
        export_job = self.get_export_job(workspace_id=workspace_id, job_id=job_id)
        if export_job is None:
            raise FileNotFoundError("Export job not found")
        if export_job.status != WorkspaceExportJobStatus.COMPLETED.value:
            raise ValueError("Export job is not completed")
        if export_job.storage_key is None:
            raise FileNotFoundError("Export artifact is missing")
        return export_job, storage.read(export_job.storage_key)

    def fail_archive_export_job(
        self,
        *,
        job: JobPayload,
        error: str,
    ) -> WorkspaceExportJob | None:
        export_job = self.get_export_job(workspace_id=job.workspace_id, job_id=job.resource_id)
        if export_job is None:
            return None
        if export_job.status == WorkspaceExportJobStatus.COMPLETED.value:
            return export_job

        export_job.status = WorkspaceExportJobStatus.FAILED.value
        export_job.error = error[:1000]
        export_job.completed_at = datetime.now(UTC)
        actor_user_id = job.requested_by_user_id
        workspace = self._session.get(Workspace, job.workspace_id)
        if actor_user_id is None and workspace is not None:
            actor_user_id = workspace.owner_user_id
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=job.workspace_id,
                user_id=actor_user_id,
                action="workspace.archive_export_job.failed",
                target_type="workspace_export_job",
                target_id=export_job.id,
                metadata={"error": export_job.error},
            )
        self._session.commit()
        self._session.refresh(export_job)
        return export_job

    def verify_archive_export_job(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        user_id: UUID,
        storage: ObjectStorage,
    ) -> dict[str, object]:
        export_job, content = self.read_export_job_content(
            workspace_id=workspace_id,
            job_id=job_id,
            storage=storage,
        )
        checked_at = datetime.now(UTC)
        actual_size = len(content)
        actual_checksum = sha256(content).hexdigest()
        checks = {
            "size_matches": export_job.size_bytes == actual_size,
            "checksum_matches": export_job.checksum_sha256 == actual_checksum,
            "zip_readable": False,
            "metadata_present": False,
            "manifest_valid": False,
            "workspace_matches": False,
            "format_version_matches": False,
        }
        metadata: dict[str, object] = {
            "filename": export_job.filename,
            "content_type": export_job.content_type,
            "expected_size_bytes": export_job.size_bytes,
            "actual_size_bytes": actual_size,
            "expected_checksum_sha256": export_job.checksum_sha256,
            "actual_checksum_sha256": actual_checksum,
        }
        manifest_counts: dict[str, int] = {}
        try:
            with ZipFile(BytesIO(content)) as archive:
                checks["zip_readable"] = True
                names = set(archive.namelist())
                checks["metadata_present"] = "metadata.json" in names
                metadata["archive_entry_count"] = len(names)
                if checks["metadata_present"]:
                    payload = json.loads(archive.read("metadata.json"))
                    manifest = payload.get("manifest") if isinstance(payload, dict) else None
                    workspace = payload.get("workspace") if isinstance(payload, dict) else None
                    if isinstance(manifest, dict):
                        checks["format_version_matches"] = (
                            manifest.get("format_version") == SUPPORTED_WORKSPACE_EXPORT_FORMAT
                        )
                        manifest_workspace_id = manifest.get("workspace_id")
                        checks["workspace_matches"] = manifest_workspace_id == str(workspace_id)
                        raw_counts = manifest.get("counts")
                        if isinstance(raw_counts, dict):
                            manifest_counts = {
                                str(key): int(value)
                                for key, value in raw_counts.items()
                                if isinstance(value, int) and value >= 0
                            }
                    elif isinstance(workspace, dict):
                        checks["workspace_matches"] = workspace.get("id") == str(workspace_id)
                    checks["manifest_valid"] = (
                        checks["metadata_present"]
                        and checks["format_version_matches"]
                        and checks["workspace_matches"]
                    )
        except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
            metadata["archive_error"] = str(exc)[:500]

        metadata["manifest_counts"] = manifest_counts
        failed_checks = [name for name, passed in checks.items() if not passed]
        verified = not failed_checks
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="workspace.archive_export_job.integrity_checked",
            target_type="workspace_export_job",
            target_id=export_job.id,
            metadata={
                "verified": verified,
                "checks": checks,
                "failed_checks": failed_checks,
                **metadata,
            },
        )
        self._session.commit()
        return {
            "workspace_id": workspace_id,
            "job_id": export_job.id,
            "verified": verified,
            "checked_at": checked_at,
            "checks": checks,
            "failed_checks": failed_checks,
            "metadata": metadata,
        }

    def run_archive_restore_drill(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        job_id: UUID,
        request: WorkspaceArchiveRestoreDrillRequest,
        storage: ObjectStorage,
    ) -> dict[str, object]:
        export_job, archive_bytes = self.read_export_job_content(
            workspace_id=workspace.id,
            job_id=job_id,
            storage=storage,
        )
        drilled_at = datetime.now(UTC)
        from backend.app.api.services.workspace_archive_import import (
            WorkspaceArchiveImportService,
        )

        preview = WorkspaceArchiveImportService(self._session).import_archive(
            workspace=workspace,
            user_id=user_id,
            archive_bytes=archive_bytes,
            request=WorkspaceArchiveImportRequest(
                dry_run=True,
                import_agents=request.import_agents,
                import_teams=request.import_teams,
                import_tasks=request.import_tasks,
                import_runtime_spaces=request.import_runtime_spaces,
                import_skill_installs=request.import_skill_installs,
                import_file_bytes=request.import_file_bytes,
                import_artifact_bytes=request.import_artifact_bytes,
                name_prefix=request.name_prefix,
                max_items_per_collection=request.max_items_per_collection,
                max_bytes_per_object=request.max_bytes_per_object,
                max_total_bytes=request.max_total_bytes,
            ),
            storage=storage,
        )
        audit_metadata = _import_preview_audit_metadata(preview)
        passed = int(audit_metadata["required_resolution_count"]) == 0
        metadata = {
            "source_export_job_id": str(export_job.id),
            "source_export_completed_at": (
                export_job.completed_at.isoformat() if export_job.completed_at is not None else None
            ),
            "passed": passed,
            **audit_metadata,
        }
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.archive_restore_drill.completed",
            target_type="workspace_export_job",
            target_id=export_job.id,
            metadata=metadata,
        )
        self._session.commit()
        return {
            "workspace_id": workspace.id,
            "job_id": export_job.id,
            "drilled_at": drilled_at,
            "passed": passed,
            "required_resolution_count": audit_metadata["required_resolution_count"],
            "suggested_resolution_count": audit_metadata["suggested_resolution_count"],
            "conflict_counts": audit_metadata["conflict_counts"],
            "import_preview": preview,
            "metadata": metadata,
        }

    def run_archive_export_job(
        self,
        *,
        job: JobPayload,
        storage: ObjectStorage,
    ) -> WorkspaceExportJob:
        export_job = self.get_export_job(workspace_id=job.workspace_id, job_id=job.resource_id)
        if export_job is None:
            raise ValueError("Export job not found")
        if export_job.status == WorkspaceExportJobStatus.COMPLETED.value:
            return export_job

        workspace = self._session.get(Workspace, job.workspace_id)
        if workspace is None:
            raise ValueError("Workspace not found")
        if export_job.workspace_id != workspace.id:
            raise ValueError("Export job workspace mismatch")

        export_job.status = WorkspaceExportJobStatus.RUNNING.value
        export_job.started_at = datetime.now(UTC)
        export_job.error = None
        self._session.commit()

        try:
            request = WorkspaceArchiveExportRequest.model_validate(export_job.request)
            actor_user_id = job.requested_by_user_id or workspace.owner_user_id
            result = WorkspaceArchiveExportBuilder(self._session).build_archive_export(
                workspace=workspace,
                user_id=actor_user_id,
                request=request,
                storage=storage,
            )
            checksum = sha256(result.content).hexdigest()
            storage_key = f"workspaces/{workspace.id}/exports/{export_job.id}/archive.zip"
            storage.write(storage_key, result.content)
            export_job.status = WorkspaceExportJobStatus.COMPLETED.value
            export_job.storage_key = storage_key
            export_job.filename = result.filename
            export_job.content_type = result.content_type
            export_job.size_bytes = len(result.content)
            export_job.checksum_sha256 = checksum
            export_job.completed_at = datetime.now(UTC)
            export_job.job_metadata = {
                **export_job.job_metadata,
                "manifest_counts": result.manifest_counts,
                "skipped_objects": result.skipped_objects,
            }
            AuditService(self._session).record_user_action(
                workspace_id=workspace.id,
                user_id=actor_user_id,
                action="workspace.archive_export_job.completed",
                target_type="workspace_export_job",
                target_id=export_job.id,
                metadata={
                    "filename": result.filename,
                    "size_bytes": len(result.content),
                    "skipped_objects": result.skipped_objects,
                },
            )
            self._session.commit()
            self._session.refresh(export_job)
            return export_job
        except Exception as exc:
            self._session.rollback()
            failed_job = self.get_export_job(workspace_id=job.workspace_id, job_id=job.resource_id)
            if failed_job is None:
                raise
            failed_job.status = WorkspaceExportJobStatus.FAILED.value
            failed_job.error = str(exc)[:1000]
            failed_job.completed_at = datetime.now(UTC)
            actor_user_id = job.requested_by_user_id or workspace.owner_user_id
            AuditService(self._session).record_user_action(
                workspace_id=job.workspace_id,
                user_id=actor_user_id,
                action="workspace.archive_export_job.failed",
                target_type="workspace_export_job",
                target_id=failed_job.id,
                metadata={"error": failed_job.error},
            )
            self._session.commit()
            raise
