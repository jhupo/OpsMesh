import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.exports import (
    WorkspaceArchiveExportRequest,
    WorkspaceArchiveExportResult,
    WorkspaceArchiveImportRequest,
    WorkspaceExportManifest,
    WorkspaceExportRequest,
    WorkspaceExportResponse,
    WorkspaceImportConflict,
    WorkspaceImportRequest,
    WorkspaceImportRequiredResolution,
    WorkspaceImportResourcePreview,
    WorkspaceImportResponse,
    WorkspaceImportSuggestedResolution,
)
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.capabilities.models import Skill, WorkspaceSkillInstall
from backend.app.exports.models import WorkspaceExportJob
from backend.app.exports.status import WorkspaceExportJobStatus
from backend.app.files.models import WorkspaceFile
from backend.app.files.security import safe_filename
from backend.app.files.storage import LocalStorage
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace

SUPPORTED_WORKSPACE_EXPORT_FORMAT = "workspace-export.v1"


class WorkspaceExportService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def build_export(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceExportRequest,
    ) -> WorkspaceExportResponse:
        included: list[str] = []
        payload: dict[str, list[dict[str, object]]] = {
            "agents": [],
            "teams": [],
            "team_members": [],
            "tasks": [],
            "task_steps": [],
            "task_messages": [],
            "runs": [],
            "run_events": [],
            "files": [],
            "artifacts": [],
            "runtime_spaces": [],
            "runtime_space_quotas": [],
            "skill_installs": [],
            "audit_events": [],
        }

        if request.include_agents:
            included.append("agents")
            payload["agents"] = self._rows(
                AgentProfile,
                workspace.id,
                request.max_items_per_collection,
                _agent_payload,
            )
        if request.include_teams:
            included.append("teams")
            payload["teams"] = self._rows(
                AgentTeam,
                workspace.id,
                request.max_items_per_collection,
                _team_payload,
            )
            payload["team_members"] = self._rows(
                AgentTeamMember,
                workspace.id,
                request.max_items_per_collection,
                _team_member_payload,
            )
        if request.include_tasks:
            included.append("tasks")
            payload["tasks"] = self._rows(
                Task,
                workspace.id,
                request.max_items_per_collection,
                _task_payload,
            )
            payload["task_steps"] = self._rows(
                TaskStep,
                workspace.id,
                request.max_items_per_collection,
                _task_step_payload,
            )
            payload["task_messages"] = self._rows(
                TaskMessage,
                workspace.id,
                request.max_items_per_collection,
                _task_message_payload,
            )
        if request.include_runs:
            included.append("runs")
            payload["runs"] = self._rows(
                AgentRun,
                workspace.id,
                request.max_items_per_collection,
                _run_payload,
            )
            payload["run_events"] = self._rows(
                RunEvent,
                workspace.id,
                request.max_items_per_collection,
                _run_event_payload,
            )
        if request.include_files:
            included.append("files")
            payload["files"] = self._rows(
                WorkspaceFile,
                workspace.id,
                request.max_items_per_collection,
                _file_payload,
            )
            payload["artifacts"] = self._rows(
                Artifact,
                workspace.id,
                request.max_items_per_collection,
                _artifact_payload,
            )
        if request.include_runtime_spaces:
            included.append("runtime_spaces")
            payload["runtime_spaces"] = self._rows(
                RuntimeSpace,
                workspace.id,
                request.max_items_per_collection,
                _runtime_space_payload,
            )
            payload["runtime_space_quotas"] = self._rows(
                RuntimeSpaceQuota,
                workspace.id,
                request.max_items_per_collection,
                _runtime_space_quota_payload,
            )
        if request.include_skill_installs:
            included.append("skill_installs")
            payload["skill_installs"] = self._rows(
                WorkspaceSkillInstall,
                workspace.id,
                request.max_items_per_collection,
                _skill_install_payload,
            )
        if request.include_audit_events:
            included.append("audit_events")
            payload["audit_events"] = self._rows(
                AuditEvent,
                workspace.id,
                request.max_items_per_collection,
                _audit_payload,
            )

        counts = {key: len(value) for key, value in payload.items()}
        export = WorkspaceExportResponse(
            manifest=WorkspaceExportManifest(
                workspace_id=workspace.id,
                exported_at=datetime.now(UTC),
                format_version=SUPPORTED_WORKSPACE_EXPORT_FORMAT,
                included_collections=included,
                counts=counts,
            ),
            workspace=_workspace_payload(workspace),
            **payload,
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.export.created",
            target_type="workspace",
            target_id=workspace.id,
            metadata={
                "format_version": export.manifest.format_version,
                "included_collections": included,
                "counts": counts,
            },
        )
        self._session.commit()
        return export

    def build_archive_export(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceArchiveExportRequest,
        storage: LocalStorage,
    ) -> WorkspaceArchiveExportResult:
        metadata = self.build_export(workspace=workspace, user_id=user_id, request=request)
        skipped: list[str] = []
        total_bytes = 0
        buffer = BytesIO()
        with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(
                "metadata.json",
                json.dumps(metadata.model_dump(mode="json"), ensure_ascii=False, indent=2),
            )
            if request.include_file_bytes:
                file_rows = self._file_rows(workspace.id, request.max_items_per_collection)
                for file in file_rows:
                    total_bytes = self._write_blob(
                        archive=archive,
                        storage=storage,
                        storage_key=file.storage_key,
                        archive_name=f"files/{file.id}/{safe_filename(file.filename)}",
                        size_bytes=file.size_bytes,
                        max_bytes_per_object=request.max_bytes_per_object,
                        max_total_bytes=request.max_total_bytes,
                        current_total=total_bytes,
                        skipped=skipped,
                    )
            if request.include_artifact_bytes:
                artifact_rows = self._artifact_rows(workspace.id, request.max_items_per_collection)
                for artifact in artifact_rows:
                    total_bytes = self._write_blob(
                        archive=archive,
                        storage=storage,
                        storage_key=artifact.storage_key,
                        archive_name=f"artifacts/{artifact.id}/{safe_filename(artifact.filename)}",
                        size_bytes=artifact.size_bytes,
                        max_bytes_per_object=request.max_bytes_per_object,
                        max_total_bytes=request.max_total_bytes,
                        current_total=total_bytes,
                        skipped=skipped,
                    )
            if skipped:
                archive.writestr("skipped-objects.json", json.dumps(skipped, indent=2))
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.archive_export.created",
            target_type="workspace",
            target_id=workspace.id,
            metadata={
                "included_file_bytes": request.include_file_bytes,
                "included_artifact_bytes": request.include_artifact_bytes,
                "skipped_objects": skipped,
            },
        )
        self._session.commit()
        return WorkspaceArchiveExportResult(
            filename=f"{workspace.slug}-workspace-archive.zip",
            content=buffer.getvalue(),
            skipped_objects=skipped,
            manifest_counts=metadata.manifest.counts,
        )

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
        storage: LocalStorage,
    ) -> tuple[WorkspaceExportJob, bytes]:
        export_job = self.get_export_job(workspace_id=workspace_id, job_id=job_id)
        if export_job is None:
            raise FileNotFoundError("Export job not found")
        if export_job.status != WorkspaceExportJobStatus.COMPLETED.value:
            raise ValueError("Export job is not completed")
        if export_job.storage_key is None:
            raise FileNotFoundError("Export artifact is missing")
        return export_job, storage.read(export_job.storage_key)

    def run_archive_export_job(
        self,
        *,
        job: JobPayload,
        storage: LocalStorage,
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
            result = self.build_archive_export(
                workspace=workspace,
                user_id=actor_user_id,
                request=request,
                storage=storage,
            )
            checksum = sha256(result.content).hexdigest()
            storage_key = (
                f"workspaces/{workspace.id}/exports/{export_job.id}/"
                f"{uuid4()}-{safe_filename(result.filename)}"
            )
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

    def import_metadata(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceImportRequest,
        record_preview: bool = True,
    ) -> WorkspaceImportResponse:
        id_map: dict[str, dict[str, str]] = {
            "agents": {},
            "teams": {},
            "team_members": {},
            "tasks": {},
            "task_steps": {},
            "task_messages": {},
            "runtime_spaces": {},
            "runtime_space_quotas": {},
            "skill_installs": {},
        }
        created_counts = {
            "agents": 0,
            "teams": 0,
            "team_members": 0,
            "tasks": 0,
            "task_steps": 0,
            "task_messages": 0,
            "runtime_spaces": 0,
            "runtime_space_quotas": 0,
            "skill_installs": 0,
        }
        skipped_counts = {
            "agents": 0,
            "teams": 0,
            "team_members": 0,
            "tasks": 0,
            "task_steps": 0,
            "task_messages": 0,
            "runtime_spaces": 0,
            "runtime_space_quotas": 0,
            "skill_installs": 0,
        }
        warnings: list[str] = []
        conflict_plan: list[WorkspaceImportConflict] = []
        preview_token = _metadata_preview_token(request)
        if request.preview_token is not None and request.preview_token != preview_token:
            conflict_plan.append(
                _preview_token_conflict(
                    source_id=str(request.export.manifest.workspace_id),
                    supplied_token=request.preview_token,
                )
            )
            response = WorkspaceImportResponse(
                dry_run=request.dry_run,
                source_workspace_id=request.export.manifest.workspace_id,
                target_workspace_id=workspace.id,
                preview_token=preview_token,
                created_counts=created_counts,
                skipped_counts=skipped_counts,
                id_map=id_map,
                warnings=["Import preview token does not match this metadata payload"],
                conflict_plan=conflict_plan,
            )
            _populate_import_preview(response, request.export)
            if request.dry_run and record_preview:
                self._record_import_preview(
                    workspace_id=workspace.id,
                    user_id=user_id,
                    action="workspace.import.previewed",
                    response=response,
                )
            return response

        unsupported_format = _unsupported_format_conflict(request.export)
        if unsupported_format is not None:
            conflict_plan.append(unsupported_format)
            response = WorkspaceImportResponse(
                dry_run=request.dry_run,
                source_workspace_id=request.export.manifest.workspace_id,
                target_workspace_id=workspace.id,
                preview_token=preview_token,
                created_counts=created_counts,
                skipped_counts=skipped_counts,
                id_map=id_map,
                warnings=["Unsupported workspace export format version"],
                conflict_plan=conflict_plan,
            )
            _populate_import_preview(response, request.export)
            if request.dry_run and record_preview:
                self._record_import_preview(
                    workspace_id=workspace.id,
                    user_id=user_id,
                    action="workspace.import.previewed",
                    response=response,
                )
            return response

        if request.import_runtime_spaces:
            for item in request.export.runtime_spaces[: request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                if _resolution_action(
                    request,
                    "runtime_spaces",
                    source_id,
                ) == "exclude_runtime_space":
                    skipped_counts["runtime_spaces"] += 1
                    continue
                imported_name = _resolved_import_name(
                    request,
                    collection="runtime_spaces",
                    source_id=source_id,
                    fallback=f"{request.name_prefix}{_string_field(item, 'name')}",
                )
                if self._runtime_space_exists(workspace.id, imported_name):
                    skipped_counts["runtime_spaces"] += 1
                    conflict_plan.append(
                        _skip_conflict(
                            collection="runtime_spaces",
                            source_id=source_id,
                            field="name",
                            source_value=_string_field(item, "name"),
                            target_value=imported_name,
                            message=(
                                f"Runtime space {imported_name!r} already exists in target "
                                "workspace."
                            ),
                        )
                    )
                    continue
                runtime_policy = _resolved_runtime_policy(request, item)
                if not runtime_policy:
                    skipped_counts["runtime_spaces"] += 1
                    conflict_plan.append(
                        _missing_runtime_policy_conflict(
                            source_id=source_id,
                            runtime_space_name=_string_field(item, "name"),
                        )
                    )
                    continue
                created_counts["runtime_spaces"] += 1
                if request.dry_run:
                    id_map["runtime_spaces"][source_id] = source_id
                    continue
                runtime_space = RuntimeSpace(
                    workspace_id=workspace.id,
                    created_by_user_id=user_id,
                    default_runtime_template_id=None,
                    name=imported_name,
                    scope=_string_field(item, "scope", "workspace"),
                    status="active",
                    policy=runtime_policy,
                    network_policy=_dict_field(item, "network_policy"),
                    storage_policy=_dict_field(item, "storage_policy"),
                    cleanup_policy=_dict_field(item, "cleanup_policy"),
                )
                self._session.add(runtime_space)
                self._session.flush()
                id_map["runtime_spaces"][source_id] = str(runtime_space.id)

            for item in request.export.runtime_space_quotas[: request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                if _resolution_action(
                    request,
                    "runtime_space_quotas",
                    source_id,
                ) == "exclude_quota":
                    skipped_counts["runtime_space_quotas"] += 1
                    continue
                runtime_space_id = id_map["runtime_spaces"].get(
                    _string_field(item, "runtime_space_id")
                )
                if runtime_space_id is None:
                    skipped_counts["runtime_space_quotas"] += 1
                    warnings.append("Skipped runtime space quota with missing imported space")
                    conflict_plan.append(
                        _missing_dependency_conflict(
                            collection="runtime_space_quotas",
                            source_id=source_id,
                            dependency="runtime_space",
                            dependency_id=_string_field(item, "runtime_space_id"),
                        )
                    )
                    continue
                quota_limit = _resolved_quota_limit(request, item)
                quota_reserved = _resolved_quota_reserved_for_validation(request, item)
                if quota_reserved > quota_limit:
                    skipped_counts["runtime_space_quotas"] += 1
                    conflict_plan.append(
                        _quota_violation_conflict(
                            collection="runtime_space_quotas",
                            source_id=source_id,
                            quota_key=_string_field(item, "quota_key"),
                            limit_value=quota_limit,
                            reserved_value=quota_reserved,
                        )
                    )
                    continue
                created_counts["runtime_space_quotas"] += 1
                if request.dry_run:
                    id_map["runtime_space_quotas"][source_id] = source_id
                    continue
                quota = RuntimeSpaceQuota(
                    workspace_id=workspace.id,
                    runtime_space_id=UUID(runtime_space_id),
                    quota_key=_string_field(item, "quota_key"),
                    limit_value=quota_limit,
                    reserved_value=0,
                    unit=_string_field(item, "unit", "count"),
                    status="active",
                )
                self._session.add(quota)
                self._session.flush()
                id_map["runtime_space_quotas"][source_id] = str(quota.id)

        if request.import_skill_installs:
            for item in request.export.skill_installs[: request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                installed_key = _string_field(item, "installed_key")
                if self._skill_install_exists(workspace.id, installed_key):
                    skipped_counts["skill_installs"] += 1
                    conflict_plan.append(
                        _skip_conflict(
                            collection="skill_installs",
                            source_id=source_id,
                            field="installed_key",
                            source_value=installed_key,
                            target_value=installed_key,
                            message=(
                                f"Skill install {installed_key!r} already exists in target "
                                "workspace."
                            ),
                        )
                    )
                    continue
                if _string_field(item, "status", "active") != "active":
                    if _resolution_action(request, "skill_installs", source_id) == "exclude_skill":
                        skipped_counts["skill_installs"] += 1
                        continue
                    skipped_counts["skill_installs"] += 1
                    conflict_plan.append(
                        _disabled_skill_install_conflict(
                            source_id=source_id,
                            installed_key=installed_key,
                            status=_string_field(item, "status", "disabled"),
                        )
                    )
                    continue
                created_counts["skill_installs"] += 1
                if request.dry_run:
                    id_map["skill_installs"][source_id] = source_id
                    continue
                skill = Skill(
                    key=f"imported.{workspace.id}.{installed_key}",
                    name=_string_field(item, "installed_name"),
                    version=_string_field(item, "installed_version"),
                    description=_string_field(item, "installed_description"),
                    capability_keys=_string_list_field(item, "installed_capability_keys"),
                    manifest=_dict_field(item, "installed_manifest"),
                    owner_workspace_id=workspace.id,
                    visibility="private",
                    status="active",
                )
                self._session.add(skill)
                self._session.flush()
                install = WorkspaceSkillInstall(
                    workspace_id=workspace.id,
                    skill_id=skill.id,
                    installed_by_user_id=user_id,
                    installed_key=installed_key,
                    installed_name=_string_field(item, "installed_name"),
                    installed_version=_string_field(item, "installed_version"),
                    installed_description=_string_field(item, "installed_description"),
                    installed_capability_keys=_string_list_field(
                        item,
                        "installed_capability_keys",
                    ),
                    installed_manifest=_dict_field(item, "installed_manifest"),
                    source_owner_workspace_id=None,
                    source_visibility=_string_field(item, "source_visibility", "public"),
                    source_checksum=_string_field(item, "source_checksum"),
                    config=_dict_field(item, "config"),
                    status="active",
                )
                self._session.add(install)
                self._session.flush()
                id_map["skill_installs"][source_id] = str(install.id)

        if request.import_agents:
            for item in request.export.agents[: request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                imported_name = _resolved_import_name(
                    request,
                    collection="agents",
                    source_id=source_id,
                    fallback=f"{request.name_prefix}{_string_field(item, 'name')}",
                )
                if self._agent_exists(workspace.id, imported_name):
                    skipped_counts["agents"] += 1
                    conflict_plan.append(
                        _skip_conflict(
                            collection="agents",
                            source_id=source_id,
                            field="name",
                            source_value=_string_field(item, "name"),
                            target_value=imported_name,
                            message=f"Agent {imported_name!r} already exists in target workspace.",
                        )
                    )
                    continue
                created_counts["agents"] += 1
                if request.dry_run:
                    id_map["agents"][source_id] = source_id
                    continue
                agent = AgentProfile(
                    workspace_id=workspace.id,
                    name=imported_name,
                    role=_string_field(item, "role"),
                    description=_string_field(item, "description"),
                    instructions=_string_field(item, "instructions"),
                    model=_string_field(item, "model", "gpt-4.1"),
                    model_settings=_dict_field(item, "model_settings"),
                    capabilities=_dict_field(item, "capabilities"),
                    skills=_remap_agent_skills(
                        _dict_field(item, "skills"),
                        id_map["skill_installs"],
                    ),
                    tool_policy=_dict_field(item, "tool_policy"),
                    runtime_policy=_dict_field(item, "runtime_policy"),
                    memory_policy=_dict_field(item, "memory_policy"),
                    approval_policy=_dict_field(item, "approval_policy"),
                    version=_int_field(item, "version", 1),
                    status="active",
                )
                self._session.add(agent)
                self._session.flush()
                id_map["agents"][source_id] = str(agent.id)

        if request.import_teams:
            for item in request.export.teams[: request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                imported_name = _resolved_import_name(
                    request,
                    collection="teams",
                    source_id=source_id,
                    fallback=f"{request.name_prefix}{_string_field(item, 'name')}",
                )
                if self._team_exists(workspace.id, imported_name):
                    skipped_counts["teams"] += 1
                    conflict_plan.append(
                        _skip_conflict(
                            collection="teams",
                            source_id=source_id,
                            field="name",
                            source_value=_string_field(item, "name"),
                            target_value=imported_name,
                            message=f"Team {imported_name!r} already exists in target workspace.",
                        )
                    )
                    continue
                created_counts["teams"] += 1
                if request.dry_run:
                    id_map["teams"][source_id] = source_id
                    continue
                manager_id = id_map["agents"].get(_string_field(item, "manager_agent_profile_id"))
                runtime_space_id = id_map["runtime_spaces"].get(
                    _string_field(item, "runtime_space_id")
                )
                team = AgentTeam(
                    workspace_id=workspace.id,
                    name=imported_name,
                    team_type=_string_field(item, "team_type", "general"),
                    description=_string_field(item, "description"),
                    manager_agent_profile_id=_uuid_or_none(manager_id),
                    runtime_space_id=_uuid_or_none(runtime_space_id),
                    coordination_rules=_dict_field(item, "coordination_rules"),
                    default_task_policy=_dict_field(item, "default_task_policy"),
                    status="active",
                )
                self._session.add(team)
                self._session.flush()
                id_map["teams"][source_id] = str(team.id)

            for item in request.export.team_members[: request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                team_id = self._resolved_dependency_id(
                    workspace_id=workspace.id,
                    request=request,
                    collection="team_members",
                    source_id=source_id,
                    source_dependency_id=_string_field(item, "agent_team_id"),
                    dependency_field="agent_team_id",
                    id_map=id_map["teams"],
                    model=AgentTeam,
                )
                agent_id = self._resolved_dependency_id(
                    workspace_id=workspace.id,
                    request=request,
                    collection="team_members",
                    source_id=source_id,
                    source_dependency_id=_string_field(item, "agent_profile_id"),
                    dependency_field="agent_profile_id",
                    id_map=id_map["agents"],
                    model=AgentProfile,
                )
                reports_to_id = id_map["team_members"].get(
                    _string_field(item, "reports_to_member_id")
                )
                if team_id is None or agent_id is None:
                    skipped_counts["team_members"] += 1
                    warnings.append("Skipped team member with missing imported team or agent")
                    conflict_plan.append(
                        _missing_dependency_conflict(
                            collection="team_members",
                            source_id=source_id,
                            dependency="team_or_agent",
                            dependency_id=",".join(
                                filter(
                                    None,
                                    [
                                        _string_field(item, "agent_team_id"),
                                        _string_field(item, "agent_profile_id"),
                                    ],
                                )
                            ),
                        )
                    )
                    continue
                created_counts["team_members"] += 1
                if request.dry_run:
                    id_map["team_members"][source_id] = source_id
                    continue
                member = AgentTeamMember(
                    workspace_id=workspace.id,
                    agent_team_id=UUID(team_id),
                    agent_profile_id=UUID(agent_id),
                    reports_to_member_id=_uuid_or_none(reports_to_id),
                    team_role=_string_field(item, "team_role"),
                    department=_optional_string_field(item, "department"),
                    position_title=_optional_string_field(item, "position_title"),
                    responsibilities=_string_list_field(item, "responsibilities"),
                    skill_weights=_dict_field(item, "skill_weights"),
                    availability=_dict_field(item, "availability"),
                    max_concurrent_tasks=_int_field(item, "max_concurrent_tasks", 1),
                    accepts_tasks=_bool_field(item, "accepts_tasks", True),
                    is_required=_bool_field(item, "is_required", True),
                    order_index=_int_field(item, "order_index", 0),
                    status=_string_field(item, "status", "active"),
                )
                self._session.add(member)
                self._session.flush()
                id_map["team_members"][source_id] = str(member.id)

        if request.import_tasks:
            for item in request.export.tasks[: request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                imported_title = _resolved_import_name(
                    request,
                    collection="tasks",
                    source_id=source_id,
                    fallback=f"{request.name_prefix}{_string_field(item, 'title')}",
                )
                if self._task_exists(workspace.id, imported_title):
                    skipped_counts["tasks"] += 1
                    conflict_plan.append(
                        _skip_conflict(
                            collection="tasks",
                            source_id=source_id,
                            field="title",
                            source_value=_string_field(item, "title"),
                            target_value=imported_title,
                            message=f"Task {imported_title!r} already exists in target workspace.",
                        )
                    )
                    continue
                created_counts["tasks"] += 1
                if request.dry_run:
                    id_map["tasks"][source_id] = source_id
                    continue
                team_id = id_map["teams"].get(_string_field(item, "agent_team_id"))
                runtime_space_id = id_map["runtime_spaces"].get(
                    _string_field(item, "runtime_space_id")
                )
                task = Task(
                    workspace_id=workspace.id,
                    created_by_user_id=user_id,
                    agent_team_id=_uuid_or_none(team_id),
                    runtime_space_id=_uuid_or_none(runtime_space_id),
                    domain_type=_string_field(item, "domain_type", "general"),
                    title=imported_title,
                    description=_string_field(item, "description"),
                    status="draft",
                    priority=_int_field(item, "priority", 0),
                    input=_dict_field(item, "input"),
                    generic_state=_dict_field(item, "generic_state"),
                    domain_state=_dict_field(item, "domain_state"),
                    team_snapshot=_optional_dict_field(item, "team_snapshot"),
                    project_plan=_optional_dict_field(item, "project_plan"),
                    final_output=_optional_dict_field(item, "final_output"),
                )
                self._session.add(task)
                self._session.flush()
                id_map["tasks"][source_id] = str(task.id)

            for item in request.export.task_steps[: request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                task_id = self._resolved_dependency_id(
                    workspace_id=workspace.id,
                    request=request,
                    collection="task_steps",
                    source_id=source_id,
                    source_dependency_id=_string_field(item, "task_id"),
                    dependency_field="task_id",
                    id_map=id_map["tasks"],
                    model=Task,
                )
                if task_id is None:
                    skipped_counts["task_steps"] += 1
                    warnings.append("Skipped task step with missing imported task")
                    conflict_plan.append(
                        _missing_dependency_conflict(
                            collection="task_steps",
                            source_id=source_id,
                            dependency="task",
                            dependency_id=_string_field(item, "task_id"),
                        )
                    )
                    continue
                created_counts["task_steps"] += 1
                if request.dry_run:
                    id_map["task_steps"][source_id] = source_id
                    continue
                agent_id = self._resolved_dependency_id(
                    workspace_id=workspace.id,
                    request=request,
                    collection="task_steps",
                    source_id=source_id,
                    source_dependency_id=_string_field(item, "assigned_agent_profile_id"),
                    dependency_field="assigned_agent_profile_id",
                    id_map=id_map["agents"],
                    model=AgentProfile,
                )
                runtime_space_id = self._resolved_dependency_id(
                    workspace_id=workspace.id,
                    request=request,
                    collection="task_steps",
                    source_id=source_id,
                    source_dependency_id=_string_field(item, "runtime_space_id"),
                    dependency_field="runtime_space_id",
                    id_map=id_map["runtime_spaces"],
                    model=RuntimeSpace,
                )
                step = TaskStep(
                    workspace_id=workspace.id,
                    task_id=UUID(task_id),
                    assigned_agent_profile_id=_uuid_or_none(agent_id),
                    runtime_space_id=_uuid_or_none(runtime_space_id),
                    work_package_id=_optional_string_field(item, "work_package_id"),
                    required_role=_optional_string_field(item, "required_role"),
                    required_skills=_string_list_field(item, "required_skills"),
                    expected_artifacts=_string_list_field(item, "expected_artifacts"),
                    acceptance_criteria=_string_list_field(item, "acceptance_criteria"),
                    review_policy=_dict_field(item, "review_policy"),
                    title=_string_field(item, "title"),
                    description=_string_field(item, "description"),
                    status="queued",
                    order_index=_int_field(item, "order_index", 0),
                    dependencies=_dict_field(item, "dependencies"),
                    result_summary=_optional_string_field(item, "result_summary"),
                )
                self._session.add(step)
                self._session.flush()
                id_map["task_steps"][source_id] = str(step.id)

            for item in request.export.task_messages[: request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                task_id = self._resolved_dependency_id(
                    workspace_id=workspace.id,
                    request=request,
                    collection="task_messages",
                    source_id=source_id,
                    source_dependency_id=_string_field(item, "task_id"),
                    dependency_field="task_id",
                    id_map=id_map["tasks"],
                    model=Task,
                )
                if task_id is None:
                    skipped_counts["task_messages"] += 1
                    warnings.append("Skipped task message with missing imported task")
                    conflict_plan.append(
                        _missing_dependency_conflict(
                            collection="task_messages",
                            source_id=source_id,
                            dependency="task",
                            dependency_id=_string_field(item, "task_id"),
                        )
                    )
                    continue
                created_counts["task_messages"] += 1
                if request.dry_run:
                    id_map["task_messages"][source_id] = source_id
                    continue
                source_step_id = _string_field(item, "task_step_id")
                source_agent_id = _string_field(item, "agent_profile_id")
                step_id = self._resolved_dependency_id(
                    workspace_id=workspace.id,
                    request=request,
                    collection="task_messages",
                    source_id=source_id,
                    source_dependency_id=source_step_id,
                    dependency_field="task_step_id",
                    id_map=id_map["task_steps"],
                    model=TaskStep,
                )
                agent_id = self._resolved_dependency_id(
                    workspace_id=workspace.id,
                    request=request,
                    collection="task_messages",
                    source_id=source_id,
                    source_dependency_id=source_agent_id,
                    dependency_field="agent_profile_id",
                    id_map=id_map["agents"],
                    model=AgentProfile,
                )
                message = TaskMessage(
                    workspace_id=workspace.id,
                    task_id=UUID(task_id),
                    task_step_id=_uuid_or_none(step_id),
                    agent_run_id=None,
                    agent_profile_id=_uuid_or_none(agent_id),
                    message_type=_string_field(item, "message_type", "note"),
                    sequence=_int_field(item, "sequence", 1),
                    body=_string_field(item, "body"),
                    payload=_dict_field(item, "payload"),
                )
                self._session.add(message)
                self._session.flush()
                id_map["task_messages"][source_id] = str(message.id)

        response = WorkspaceImportResponse(
            dry_run=request.dry_run,
            source_workspace_id=request.export.manifest.workspace_id,
            target_workspace_id=workspace.id,
            preview_token=preview_token,
            created_counts=created_counts,
            skipped_counts=skipped_counts,
            id_map=id_map,
            warnings=warnings,
            conflict_plan=conflict_plan,
        )
        _populate_import_preview(response, request.export)
        if request.dry_run:
            self._session.rollback()
            if record_preview:
                self._record_import_preview(
                    workspace_id=workspace.id,
                    user_id=user_id,
                    action="workspace.import.previewed",
                    response=response,
                )
            return response
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.import.created",
            target_type="workspace",
            target_id=workspace.id,
            metadata={
                "source_workspace_id": str(request.export.manifest.workspace_id),
                "created_counts": created_counts,
                "skipped_counts": skipped_counts,
            },
        )
        self._session.commit()
        return response

    def import_archive(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        archive_bytes: bytes,
        request: WorkspaceArchiveImportRequest,
        storage: LocalStorage,
    ) -> WorkspaceImportResponse:
        try:
            archive = ZipFile(BytesIO(archive_bytes))
        except BadZipFile as exc:
            raise ValueError("Archive is not a valid zip file") from exc
        with archive:
            if "metadata.json" not in archive.namelist():
                raise ValueError("Archive is missing metadata.json")
            metadata = WorkspaceExportResponse.model_validate_json(archive.read("metadata.json"))
            archive_names = set(archive.namelist())
            response = self.import_metadata(
                workspace=workspace,
                user_id=user_id,
                request=WorkspaceImportRequest(
                    export=metadata,
                    dry_run=request.dry_run,
                    import_agents=request.import_agents,
                    import_teams=request.import_teams,
                    import_tasks=request.import_tasks,
                    import_runtime_spaces=request.import_runtime_spaces,
                    import_skill_installs=request.import_skill_installs,
                    name_prefix=request.name_prefix,
                    max_items_per_collection=request.max_items_per_collection,
                ),
                record_preview=False,
            )
            response.created_counts.setdefault("files", 0)
            response.skipped_counts.setdefault("files", 0)
            response.id_map.setdefault("files", {})
            response.created_counts.setdefault("artifacts", 0)
            response.skipped_counts.setdefault("artifacts", 0)
            response.id_map.setdefault("artifacts", {})
            total_bytes = 0
            if request.import_file_bytes:
                for item in metadata.files[: request.max_items_per_collection]:
                    total_bytes = self._import_workspace_file_blob(
                        workspace=workspace,
                        user_id=user_id,
                        request=request,
                        archive=archive,
                        archive_names=archive_names,
                        item=item,
                        response=response,
                        storage=storage,
                        total_bytes=total_bytes,
                    )
            if request.import_artifact_bytes:
                for item in metadata.artifacts[: request.max_items_per_collection]:
                    total_bytes = self._import_artifact_blob(
                        workspace=workspace,
                        request=request,
                        archive=archive,
                        archive_names=archive_names,
                        item=item,
                        response=response,
                        storage=storage,
                        total_bytes=total_bytes,
                    )
            _populate_import_preview(response, metadata)
            if request.dry_run:
                self._session.rollback()
                self._record_import_preview(
                    workspace_id=workspace.id,
                    user_id=user_id,
                    action="workspace.archive_import.previewed",
                    response=response,
                )
                return response
            AuditService(self._session).record_user_action(
                workspace_id=workspace.id,
                user_id=user_id,
                action="workspace.archive_import.created",
                target_type="workspace",
                target_id=workspace.id,
                metadata={
                    "source_workspace_id": str(metadata.manifest.workspace_id),
                    "created_counts": response.created_counts,
                    "skipped_counts": response.skipped_counts,
                },
            )
            self._session.commit()
            return response

    def _record_import_preview(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        action: str,
        response: WorkspaceImportResponse,
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            target_type="workspace",
            target_id=workspace_id,
            metadata=_import_preview_audit_metadata(response),
        )
        self._session.commit()

    def _import_workspace_file_blob(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceArchiveImportRequest,
        archive: ZipFile,
        archive_names: set[str],
        item: dict[str, object],
        response: WorkspaceImportResponse,
        storage: LocalStorage,
        total_bytes: int,
    ) -> int:
        source_id = _string_field(item, "id")
        if _archive_resolution_action(request, "files", source_id) == "exclude_object":
            response.skipped_counts["files"] += 1
            return total_bytes
        filename = safe_filename(_string_field(item, "filename", "file.bin"))
        archive_name = f"files/{source_id}/{filename}"
        content = self._read_import_blob(
            archive=archive,
            archive_names=archive_names,
            archive_name=archive_name,
            source_id=source_id,
            collection="files",
            response=response,
            request=request,
            total_bytes=total_bytes,
        )
        if content is None:
            return total_bytes
        checksum_result = _validated_checksum(
            content=content,
            source_checksum=_string_field(item, "checksum_sha256"),
            source_id=source_id,
            collection="files",
            response=response,
            allow_replace=_archive_resolution_action(
                request,
                "files",
                source_id,
            )
            == "replace_archive_object",
        )
        if not checksum_result.matched:
            response.skipped_counts["files"] += 1
            return total_bytes
        response.created_counts["files"] += 1
        total_bytes += len(content)
        if request.dry_run:
            return total_bytes
        imported_filename = safe_filename(f"{request.name_prefix}{filename}")
        file = WorkspaceFile(
            workspace_id=workspace.id,
            uploaded_by_user_id=user_id,
            filename=imported_filename,
            content_type=_string_field(item, "content_type", "application/octet-stream"),
            size_bytes=len(content),
            checksum_sha256=checksum_result.checksum_sha256,
            storage_key=(
                f"workspaces/{workspace.id}/files/imported/{source_id}/{imported_filename}"
            ),
            status="active",
            file_metadata={
                **_dict_field(item, "metadata"),
                "imported_from_file_id": source_id,
                "import_checksum_matched": checksum_result.matched,
            },
        )
        self._session.add(file)
        self._session.flush()
        storage.write(file.storage_key, content)
        response.id_map["files"][source_id] = str(file.id)
        return total_bytes

    def _import_artifact_blob(
        self,
        *,
        workspace: Workspace,
        request: WorkspaceArchiveImportRequest,
        archive: ZipFile,
        archive_names: set[str],
        item: dict[str, object],
        response: WorkspaceImportResponse,
        storage: LocalStorage,
        total_bytes: int,
    ) -> int:
        source_id = _string_field(item, "id")
        if _archive_resolution_action(request, "artifacts", source_id) == "exclude_object":
            response.skipped_counts["artifacts"] += 1
            return total_bytes
        filename = safe_filename(_string_field(item, "filename", "artifact.bin"))
        archive_name = f"artifacts/{source_id}/{filename}"
        content = self._read_import_blob(
            archive=archive,
            archive_names=archive_names,
            archive_name=archive_name,
            source_id=source_id,
            collection="artifacts",
            response=response,
            request=request,
            total_bytes=total_bytes,
        )
        if content is None:
            return total_bytes
        checksum_result = _validated_checksum(
            content=content,
            source_checksum=_string_field(item, "checksum_sha256"),
            source_id=source_id,
            collection="artifacts",
            response=response,
            allow_replace=_archive_resolution_action(
                request,
                "artifacts",
                source_id,
            )
            == "replace_archive_object",
        )
        if not checksum_result.matched:
            response.skipped_counts["artifacts"] += 1
            return total_bytes
        response.created_counts["artifacts"] += 1
        total_bytes += len(content)
        if request.dry_run:
            return total_bytes
        source_task_id = _string_field(item, "task_id")
        source_run_id = _string_field(item, "agent_run_id")
        source_step_id = _string_field(item, "task_step_id")
        source_agent_id = _string_field(item, "agent_profile_id")
        source_supersedes_id = _string_field(item, "supersedes_artifact_id")
        imported_task_id = response.id_map["tasks"].get(source_task_id)
        imported_run_id = response.id_map.get("runs", {}).get(source_run_id)
        imported_step_id = response.id_map.get("task_steps", {}).get(source_step_id)
        imported_agent_id = response.id_map.get("agents", {}).get(source_agent_id)
        imported_supersedes_id = response.id_map["artifacts"].get(source_supersedes_id)
        if source_task_id and imported_task_id is None:
            response.warnings.append(
                f"Imported artifact {source_id} without a mapped task"
            )
        if source_run_id and imported_run_id is None:
            response.warnings.append(
                f"Imported artifact {source_id} without a mapped run"
            )
        imported_filename = safe_filename(f"{request.name_prefix}{filename}")
        artifact = Artifact(
            workspace_id=workspace.id,
            task_id=_uuid_or_none(imported_task_id),
            agent_run_id=None,
            task_step_id=_uuid_or_none(imported_step_id),
            agent_profile_id=_uuid_or_none(imported_agent_id),
            supersedes_artifact_id=_uuid_or_none(imported_supersedes_id),
            work_package_id=_optional_string_field(item, "work_package_id"),
            version=_int_field(item, "version", 1),
            review_status=_string_field(item, "review_status", "pending"),
            artifact_type=_string_field(item, "artifact_type", "file"),
            filename=imported_filename,
            content_type=_string_field(item, "content_type", "application/octet-stream"),
            size_bytes=len(content),
            checksum_sha256=checksum_result.checksum_sha256,
            storage_key=(
                f"workspaces/{workspace.id}/artifacts/imported/{source_id}/"
                f"{imported_filename}"
            ),
            artifact_metadata={
                **_dict_field(item, "metadata"),
                "imported_from_artifact_id": source_id,
                "source_task_id": source_task_id or None,
                "source_agent_run_id": source_run_id or None,
                "source_task_step_id": source_step_id or None,
                "source_agent_profile_id": source_agent_id or None,
                "source_supersedes_artifact_id": source_supersedes_id or None,
                "imported_task_id": imported_task_id,
                "imported_agent_run_id": imported_run_id,
                "imported_task_step_id": imported_step_id,
                "imported_agent_profile_id": imported_agent_id,
                "imported_supersedes_artifact_id": imported_supersedes_id,
                "import_checksum_matched": checksum_result.matched,
            },
            created_at=datetime.now(UTC),
        )
        self._session.add(artifact)
        self._session.flush()
        storage.write(artifact.storage_key, content)
        response.id_map["artifacts"][source_id] = str(artifact.id)
        return total_bytes

    def _read_import_blob(
        self,
        *,
        archive: ZipFile,
        archive_names: set[str],
        archive_name: str,
        source_id: str,
        collection: str,
        response: WorkspaceImportResponse,
        request: WorkspaceArchiveImportRequest,
        total_bytes: int,
    ) -> bytes | None:
        if archive_name not in archive_names:
            response.skipped_counts[collection] += 1
            response.warnings.append(f"Skipped {collection[:-1]} {source_id}: bytes not found")
            response.conflict_plan.append(
                WorkspaceImportConflict(
                    collection=collection,
                    source_id=source_id,
                    field="bytes",
                    strategy="skip",
                    severity="warning",
                    message=f"{collection[:-1].title()} bytes are missing from the archive.",
                )
            )
            return None
        content = archive.read(archive_name)
        if len(content) > request.max_bytes_per_object:
            response.skipped_counts[collection] += 1
            response.warnings.append(f"Skipped {collection[:-1]} {source_id}: object too large")
            response.conflict_plan.append(
                WorkspaceImportConflict(
                    collection=collection,
                    source_id=source_id,
                    field="size_bytes",
                    source_value=str(len(content)),
                    target_value=str(request.max_bytes_per_object),
                    strategy="reject",
                    severity="error",
                    message=(
                        f"{collection[:-1].title()} exceeds max_bytes_per_object "
                        f"({len(content)} > {request.max_bytes_per_object})."
                    ),
                )
            )
            return None
        if total_bytes + len(content) > request.max_total_bytes:
            response.skipped_counts[collection] += 1
            response.warnings.append(
                f"Skipped {collection[:-1]} {source_id}: archive byte limit reached"
            )
            response.conflict_plan.append(
                WorkspaceImportConflict(
                    collection=collection,
                    source_id=source_id,
                    field="total_bytes",
                    source_value=str(total_bytes + len(content)),
                    target_value=str(request.max_total_bytes),
                    strategy="reject",
                    severity="error",
                    message=(
                        f"Archive import would exceed max_total_bytes "
                        f"({total_bytes + len(content)} > {request.max_total_bytes})."
                    ),
                )
            )
            return None
        return content

    def _rows(
        self,
        model: type[Any],
        workspace_id: UUID,
        limit: int,
        serializer: Any,
    ) -> list[dict[str, object]]:
        rows = self._session.scalars(
            select(model).where(model.workspace_id == workspace_id).limit(limit)
        ).all()
        return [serializer(row) for row in rows]

    def _file_rows(self, workspace_id: UUID, limit: int) -> list[WorkspaceFile]:
        return list(
            self._session.scalars(
                select(WorkspaceFile)
                .where(WorkspaceFile.workspace_id == workspace_id, WorkspaceFile.status == "active")
                .limit(limit)
            )
        )

    def _artifact_rows(self, workspace_id: UUID, limit: int) -> list[Artifact]:
        return list(
            self._session.scalars(
                select(Artifact).where(Artifact.workspace_id == workspace_id).limit(limit)
            )
        )

    def _write_blob(
        self,
        *,
        archive: ZipFile,
        storage: LocalStorage,
        storage_key: str,
        archive_name: str,
        size_bytes: int,
        max_bytes_per_object: int,
        max_total_bytes: int,
        current_total: int,
        skipped: list[str],
    ) -> int:
        if size_bytes > max_bytes_per_object:
            skipped.append(f"{archive_name}: object exceeds max_bytes_per_object")
            return current_total
        if current_total + size_bytes > max_total_bytes:
            skipped.append(f"{archive_name}: archive exceeds max_total_bytes")
            return current_total
        try:
            content = storage.read(storage_key)
        except FileNotFoundError:
            skipped.append(f"{archive_name}: storage object missing")
            return current_total
        archive.writestr(archive_name, content)
        return current_total + len(content)

    def _agent_exists(self, workspace_id: UUID, name: str) -> bool:
        return self._session.scalar(
            select(AgentProfile.id).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.name == name,
            )
        ) is not None

    def _team_exists(self, workspace_id: UUID, name: str) -> bool:
        return self._session.scalar(
            select(AgentTeam.id).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.name == name,
            )
        ) is not None

    def _task_exists(self, workspace_id: UUID, title: str) -> bool:
        return self._session.scalar(
            select(Task.id).where(Task.workspace_id == workspace_id, Task.title == title)
        ) is not None

    def _runtime_space_exists(self, workspace_id: UUID, name: str) -> bool:
        return self._session.scalar(
            select(RuntimeSpace.id).where(
                RuntimeSpace.workspace_id == workspace_id,
                RuntimeSpace.name == name,
            )
        ) is not None

    def _skill_install_exists(self, workspace_id: UUID, installed_key: str) -> bool:
        return self._session.scalar(
            select(WorkspaceSkillInstall.id).where(
                WorkspaceSkillInstall.workspace_id == workspace_id,
                WorkspaceSkillInstall.installed_key == installed_key,
            )
        ) is not None

    def _resolved_dependency_id(
        self,
        *,
        workspace_id: UUID,
        request: WorkspaceImportRequest,
        collection: str,
        source_id: str,
        source_dependency_id: str,
        dependency_field: str,
        id_map: dict[str, str],
        model: type[Any],
    ) -> str | None:
        if not source_dependency_id:
            return None
        mapped_id = id_map.get(source_dependency_id)
        if mapped_id is not None:
            return mapped_id
        resolution = _resolution(request, collection, source_id)
        dependencies = resolution.get("dependencies")
        if resolution.get("action") != "import_dependency" or not isinstance(
            dependencies,
            dict,
        ):
            return None
        target_id = dependencies.get(dependency_field)
        if not isinstance(target_id, str) or not _is_valid_uuid(target_id):
            return None
        exists = self._session.scalar(
            select(model.id).where(
                model.workspace_id == workspace_id,
                model.id == UUID(target_id),
            )
        )
        return target_id if exists is not None else None


@dataclass(frozen=True)
class _ChecksumResult:
    checksum_sha256: str
    matched: bool


def _skip_conflict(
    *,
    collection: str,
    source_id: str,
    field: str,
    source_value: str,
    target_value: str,
    message: str,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection=collection,
        source_id=source_id,
        field=field,
        source_value=source_value,
        target_value=target_value,
        strategy="skip_existing",
        severity="warning",
        message=message,
    )


def _missing_dependency_conflict(
    *,
    collection: str,
    source_id: str,
    dependency: str,
    dependency_id: str,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection=collection,
        source_id=source_id,
        field=f"{dependency}_id",
        source_value=dependency_id,
        strategy="skip_missing_dependency",
        severity="warning",
        message=(
            f"Skipped {collection[:-1].replace('_', ' ')} because imported "
            f"{dependency.replace('_', ' ')} {dependency_id!r} is unavailable."
        ),
    )


def _unsupported_format_conflict(
    export: WorkspaceExportResponse,
) -> WorkspaceImportConflict | None:
    if export.manifest.format_version == SUPPORTED_WORKSPACE_EXPORT_FORMAT:
        return None
    return WorkspaceImportConflict(
        collection="manifest",
        source_id=str(export.manifest.workspace_id),
        field="format_version",
        source_value=export.manifest.format_version,
        target_value=SUPPORTED_WORKSPACE_EXPORT_FORMAT,
        strategy="reject",
        severity="error",
        message=(
            f"Workspace export format {export.manifest.format_version!r} is not supported; "
            f"expected {SUPPORTED_WORKSPACE_EXPORT_FORMAT!r}."
        ),
    )


def _disabled_skill_install_conflict(
    *,
    source_id: str,
    installed_key: str,
    status: str,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection="skill_installs",
        source_id=source_id,
        field="status",
        source_value=status,
        target_value="active",
        strategy="reject",
        severity="error",
        message=(
            f"Skill install {installed_key!r} is {status!r} in the source export; "
            "importing it as active would change the source workspace safety policy."
        ),
    )


def _missing_runtime_policy_conflict(
    *,
    source_id: str,
    runtime_space_name: str,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection="runtime_spaces",
        source_id=source_id,
        field="policy",
        source_value="{}",
        strategy="reject",
        severity="error",
        message=(
            f"Runtime space {runtime_space_name!r} has no runtime policy in the source export; "
            "import requires an explicit policy before this space can be created."
        ),
    )


def _quota_violation_conflict(
    *,
    collection: str,
    source_id: str,
    quota_key: str,
    limit_value: int,
    reserved_value: int,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection=collection,
        source_id=source_id,
        field="reserved_value",
        source_value=str(reserved_value),
        target_value=str(limit_value),
        strategy="reject",
        severity="error",
        message=(
            f"Quota {quota_key!r} reserves {reserved_value}, which exceeds its limit "
            f"{limit_value}; import requires a consistent quota before commit."
        ),
    )


def _checksum_conflict(
    *,
    collection: str,
    source_id: str,
    source_checksum: str,
    actual_checksum: str,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection=collection,
        source_id=source_id,
        field="checksum_sha256",
        source_value=source_checksum,
        target_value=actual_checksum,
        strategy="reject",
        severity="error",
        message=(
            f"{collection[:-1].replace('_', ' ').title()} checksum mismatch for "
            f"{source_id}: expected {source_checksum}, got {actual_checksum}."
        ),
    )


def _preview_token_conflict(
    *,
    source_id: str,
    supplied_token: str,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection="manifest",
        source_id=source_id,
        field="preview_token",
        source_value=supplied_token,
        target_value=None,
        strategy="reject",
        severity="error",
        message="Import preview token does not match the supplied metadata payload.",
    )


def _populate_import_preview(
    response: WorkspaceImportResponse,
    export: WorkspaceExportResponse,
) -> None:
    conflict_counts = _conflict_counts(response.conflict_plan)
    required_resolutions = _required_resolutions(response.conflict_plan)
    suggested_resolutions = _suggested_resolutions(response.conflict_plan)
    response.required_resolutions = required_resolutions
    response.suggested_resolutions = suggested_resolutions
    required_counts = _conflict_counts(required_resolutions)
    response.resources = [
        WorkspaceImportResourcePreview(
            collection=collection,
            source_count=_source_count(export, collection),
            create_count=response.created_counts.get(collection, 0),
            skip_count=response.skipped_counts.get(collection, 0),
            conflict_count=conflict_counts.get(collection, 0),
            action=_preview_action(
                response.created_counts.get(collection, 0),
                response.skipped_counts.get(collection, 0),
                required_counts.get(collection, 0),
            ),
            required_resolution_count=required_counts.get(collection, 0),
        )
        for collection in _preview_collections(export, response)
    ]
    response.estimated_counts = {
        "source_total": sum(item.source_count for item in response.resources),
        "create_total": sum(item.create_count for item in response.resources),
        "skip_total": sum(item.skip_count for item in response.resources),
        "conflict_total": len(response.conflict_plan),
        "required_resolution_total": len(required_resolutions),
        "suggested_resolution_total": len(suggested_resolutions),
    }


def _import_preview_audit_metadata(response: WorkspaceImportResponse) -> dict[str, object]:
    conflict_counts = _conflict_counts(response.conflict_plan)
    required_counts = _conflict_counts(response.required_resolutions)
    severity_counts = Counter(conflict.severity for conflict in response.conflict_plan)
    strategy_counts = Counter(conflict.strategy for conflict in response.conflict_plan)
    return {
        "source_workspace_id": str(response.source_workspace_id),
        "dry_run": True,
        "created_counts": dict(response.created_counts),
        "skipped_counts": dict(response.skipped_counts),
        "conflict_counts": dict(sorted(conflict_counts.items())),
        "conflict_severity_counts": dict(sorted(severity_counts.items())),
        "conflict_strategy_counts": dict(sorted(strategy_counts.items())),
        "required_resolution_count": len(response.required_resolutions),
        "required_resolution_counts": dict(sorted(required_counts.items())),
        "suggested_resolution_count": len(response.suggested_resolutions),
        "conflict_summaries": [
            {
                "collection": conflict.collection,
                "field": conflict.field,
                "strategy": conflict.strategy,
                "severity": conflict.severity,
            }
            for conflict in response.conflict_plan[:10]
        ],
    }


def _preview_collections(
    export: WorkspaceExportResponse,
    response: WorkspaceImportResponse,
) -> list[str]:
    collections = [
        "manifest",
        "runtime_spaces",
        "runtime_space_quotas",
        "skill_installs",
        "agents",
        "teams",
        "team_members",
        "tasks",
        "task_steps",
        "task_messages",
        "files",
        "artifacts",
    ]
    return [
        collection
        for collection in collections
        if _source_count(export, collection) > 0
        or response.created_counts.get(collection, 0) > 0
        or response.skipped_counts.get(collection, 0) > 0
    ]


def _source_count(export: WorkspaceExportResponse, collection: str) -> int:
    if collection == "manifest":
        return 1
    value = getattr(export, collection, None)
    return len(value) if isinstance(value, list) else 0


def _conflict_counts(
    items: list[WorkspaceImportConflict] | list[WorkspaceImportRequiredResolution],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.collection] = counts.get(item.collection, 0) + 1
    return counts


def _required_resolutions(
    conflicts: list[WorkspaceImportConflict],
) -> list[WorkspaceImportRequiredResolution]:
    return [
        WorkspaceImportRequiredResolution(
            collection=conflict.collection,
            source_id=conflict.source_id,
            field=conflict.field,
            reason=conflict.strategy,
            allowed_actions=_allowed_resolution_actions(conflict),
            message=conflict.message,
        )
        for conflict in conflicts
        if conflict.severity == "error" or conflict.strategy == "reject"
    ]


def _suggested_resolutions(
    conflicts: list[WorkspaceImportConflict],
) -> list[WorkspaceImportSuggestedResolution]:
    return [
        WorkspaceImportSuggestedResolution(
            collection=conflict.collection,
            source_id=conflict.source_id,
            field=conflict.field,
            reason=conflict.strategy,
            allowed_actions=_allowed_resolution_actions(conflict),
            message=conflict.message,
            resolution_key=f"{conflict.collection}:{conflict.source_id}",
            recommended_action=_recommended_resolution_action(conflict),
            resolution_template=_resolution_template(conflict),
        )
        for conflict in conflicts
        if _allowed_resolution_actions(conflict) != ["skip"]
    ]


def _recommended_resolution_action(conflict: WorkspaceImportConflict) -> str:
    if conflict.strategy == "skip_existing" and conflict.field in {"name", "title"}:
        return "rename"
    if conflict.field == "preview_token":
        return "rerun_preview"
    if conflict.field == "checksum_sha256":
        return "replace_archive_object"
    if conflict.field in {"size_bytes", "total_bytes"}:
        return "exclude_object"
    if conflict.field == "format_version":
        return "export_supported_version"
    if conflict.collection == "skill_installs" and conflict.field == "status":
        return "exclude_skill"
    if conflict.collection == "runtime_spaces" and conflict.field == "policy":
        return "add_runtime_policy"
    if conflict.field == "reserved_value":
        return "release_source_reservations"
    if conflict.strategy == "skip_missing_dependency":
        return "import_dependency"
    if conflict.strategy == "reject":
        return "fix_source"
    return "skip"


def _resolution_template(conflict: WorkspaceImportConflict) -> dict[str, object]:
    action = _recommended_resolution_action(conflict)
    if action == "rename":
        return {
            "action": action,
            "new_name": _suggested_rename_value(conflict),
        }
    if action == "add_runtime_policy":
        return {
            "action": action,
            "policy": {"runtime_modes": ["docker"], "network": "restricted"},
        }
    if action == "increase_quota_limit":
        return {
            "action": action,
            "limit_value": _int_from_optional_string(conflict.source_value, 0),
        }
    if action == "import_dependency":
        return {
            "action": action,
            "dependency_field": conflict.field,
            "dependency_id": conflict.source_value,
        }
    if action in {
        "exclude_skill",
        "exclude_runtime_space",
        "exclude_quota",
        "exclude_object",
        "release_source_reservations",
        "replace_archive_object",
        "rerun_preview",
        "export_supported_version",
        "fix_source",
        "skip",
    }:
        return {"action": action}
    return {"action": action}


def _suggested_rename_value(conflict: WorkspaceImportConflict) -> str:
    value = conflict.target_value or conflict.source_value or conflict.source_id
    return f"{value} 2"[:160]


def _allowed_resolution_actions(conflict: WorkspaceImportConflict) -> list[str]:
    if conflict.strategy == "skip_existing" and conflict.field in {"name", "title"}:
        return ["rename", "skip"]
    if conflict.field == "preview_token":
        return ["rerun_preview", "commit_without_token"]
    if conflict.field == "size_bytes":
        return ["increase_max_bytes_per_object", "exclude_object"]
    if conflict.field == "total_bytes":
        return ["increase_max_total_bytes", "exclude_object"]
    if conflict.field == "checksum_sha256":
        return ["replace_archive_object", "exclude_object"]
    if conflict.field == "format_version":
        return ["export_supported_version", "cancel_import"]
    if conflict.collection == "skill_installs" and conflict.field == "status":
        return ["exclude_skill", "enable_in_source_and_reexport"]
    if conflict.collection == "runtime_spaces" and conflict.field == "policy":
        return ["add_runtime_policy", "exclude_runtime_space"]
    if conflict.field == "reserved_value":
        return ["increase_quota_limit", "release_source_reservations", "exclude_quota"]
    if conflict.strategy == "skip_missing_dependency":
        return ["import_dependency", "skip"]
    if conflict.strategy == "reject":
        return ["fix_source", "exclude_object"]
    return ["skip"]


def _preview_action(create_count: int, skip_count: int, required_count: int) -> str:
    if required_count > 0:
        return "requires_resolution"
    if create_count > 0 and skip_count > 0:
        return "partial_import"
    if create_count > 0:
        return "create"
    if skip_count > 0:
        return "skip"
    return "none"


def _resolved_import_name(
    request: WorkspaceImportRequest,
    *,
    collection: str,
    source_id: str,
    fallback: str,
) -> str:
    resolution = request.resolutions.get(f"{collection}:{source_id}")
    if not isinstance(resolution, dict):
        return fallback
    if resolution.get("action") != "rename":
        return fallback
    new_name = resolution.get("new_name")
    if not isinstance(new_name, str) or not new_name.strip():
        return fallback
    return new_name.strip()[:160]


def _resolution_action(
    request: WorkspaceImportRequest,
    collection: str,
    source_id: str,
) -> str | None:
    resolution = _resolution(request, collection, source_id)
    action = resolution.get("action")
    return action if isinstance(action, str) else None


def _resolution(
    request: WorkspaceImportRequest,
    collection: str,
    source_id: str,
) -> dict[str, object]:
    resolution = request.resolutions.get(f"{collection}:{source_id}")
    return resolution if isinstance(resolution, dict) else {}


def _resolved_runtime_policy(
    request: WorkspaceImportRequest,
    item: dict[str, object],
) -> dict[str, object]:
    source_policy = _dict_field(item, "policy")
    source_id = _string_field(item, "id")
    resolution = _resolution(request, "runtime_spaces", source_id)
    if resolution.get("action") != "add_runtime_policy":
        return source_policy
    policy = resolution.get("policy")
    return policy if isinstance(policy, dict) else source_policy


def _resolved_quota_limit(
    request: WorkspaceImportRequest,
    item: dict[str, object],
) -> int:
    source_id = _string_field(item, "id")
    source_limit = _int_field(item, "limit_value", 0)
    source_reserved = _int_field(item, "reserved_value", 0)
    action = _resolution_action(request, "runtime_space_quotas", source_id)
    if action != "increase_quota_limit":
        return source_limit
    resolution = _resolution(request, "runtime_space_quotas", source_id)
    raw_limit = resolution.get("limit_value")
    if isinstance(raw_limit, int) and raw_limit >= source_reserved:
        return raw_limit
    return source_limit


def _resolved_quota_reserved_for_validation(
    request: WorkspaceImportRequest,
    item: dict[str, object],
) -> int:
    source_id = _string_field(item, "id")
    if _resolution_action(request, "runtime_space_quotas", source_id) == (
        "release_source_reservations"
    ):
        return 0
    return _int_field(item, "reserved_value", 0)


def _archive_resolution_action(
    request: WorkspaceArchiveImportRequest,
    collection: str,
    source_id: str,
) -> str | None:
    resolution = request.resolutions.get(f"{collection}:{source_id}")
    if not isinstance(resolution, dict):
        return None
    action = resolution.get("action")
    return action if isinstance(action, str) else None


def _metadata_preview_token(request: WorkspaceImportRequest) -> str:
    payload = {
        "export": request.export.model_dump(mode="json"),
        "import_agents": request.import_agents,
        "import_teams": request.import_teams,
        "import_tasks": request.import_tasks,
        "import_runtime_spaces": request.import_runtime_spaces,
        "import_skill_installs": request.import_skill_installs,
        "max_items_per_collection": request.max_items_per_collection,
        "name_prefix": request.name_prefix,
    }
    content = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(content.encode("utf-8")).hexdigest()


def _workspace_payload(workspace: Workspace) -> dict[str, object]:
    return {
        "id": str(workspace.id),
        "owner_user_id": str(workspace.owner_user_id),
        "name": workspace.name,
        "slug": workspace.slug,
        "settings": workspace.settings,
        "status": workspace.status,
        "created_at": _dt(workspace.created_at),
        "updated_at": _dt(workspace.updated_at),
    }


def _agent_payload(agent: AgentProfile) -> dict[str, object]:
    return {
        "id": str(agent.id),
        "workspace_id": str(agent.workspace_id),
        "name": agent.name,
        "role": agent.role,
        "description": agent.description,
        "instructions": agent.instructions,
        "model": agent.model,
        "model_settings": agent.model_settings,
        "capabilities": agent.capabilities,
        "skills": agent.skills,
        "tool_policy": agent.tool_policy,
        "runtime_policy": agent.runtime_policy,
        "memory_policy": agent.memory_policy,
        "approval_policy": agent.approval_policy,
        "version": agent.version,
        "status": agent.status,
        "created_at": _dt(agent.created_at),
        "updated_at": _dt(agent.updated_at),
    }


def _team_payload(team: AgentTeam) -> dict[str, object]:
    return {
        "id": str(team.id),
        "workspace_id": str(team.workspace_id),
        "name": team.name,
        "team_type": team.team_type,
        "description": team.description,
        "manager_agent_profile_id": _str_or_none(team.manager_agent_profile_id),
        "runtime_space_id": _str_or_none(team.runtime_space_id),
        "coordination_rules": team.coordination_rules,
        "default_task_policy": team.default_task_policy,
        "status": team.status,
        "created_at": _dt(team.created_at),
        "updated_at": _dt(team.updated_at),
    }


def _team_member_payload(member: AgentTeamMember) -> dict[str, object]:
    return {
        "id": str(member.id),
        "workspace_id": str(member.workspace_id),
        "agent_team_id": str(member.agent_team_id),
        "agent_profile_id": str(member.agent_profile_id),
        "reports_to_member_id": _str_or_none(member.reports_to_member_id),
        "team_role": member.team_role,
        "department": member.department,
        "position_title": member.position_title,
        "responsibilities": member.responsibilities,
        "skill_weights": member.skill_weights,
        "availability": member.availability,
        "max_concurrent_tasks": member.max_concurrent_tasks,
        "accepts_tasks": member.accepts_tasks,
        "is_required": member.is_required,
        "order_index": member.order_index,
        "status": member.status,
    }


def _task_payload(task: Task) -> dict[str, object]:
    return {
        "id": str(task.id),
        "workspace_id": str(task.workspace_id),
        "created_by_user_id": _str_or_none(task.created_by_user_id),
        "created_by_agent_run_id": _str_or_none(task.created_by_agent_run_id),
        "agent_team_id": _str_or_none(task.agent_team_id),
        "runtime_space_id": _str_or_none(task.runtime_space_id),
        "domain_type": task.domain_type,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "input": task.input,
        "generic_state": task.generic_state,
        "domain_state": task.domain_state,
        "team_snapshot": task.team_snapshot,
        "project_plan": task.project_plan,
        "final_output": task.final_output,
        "completed_at": _dt_or_none(task.completed_at),
        "created_at": _dt(task.created_at),
        "updated_at": _dt(task.updated_at),
    }


def _task_step_payload(step: TaskStep) -> dict[str, object]:
    return {
        "id": str(step.id),
        "workspace_id": str(step.workspace_id),
        "task_id": str(step.task_id),
        "assigned_agent_profile_id": _str_or_none(step.assigned_agent_profile_id),
        "runtime_space_id": _str_or_none(step.runtime_space_id),
        "work_package_id": step.work_package_id,
        "required_role": step.required_role,
        "required_skills": step.required_skills,
        "expected_artifacts": step.expected_artifacts,
        "acceptance_criteria": step.acceptance_criteria,
        "review_policy": step.review_policy,
        "title": step.title,
        "description": step.description,
        "status": step.status,
        "order_index": step.order_index,
        "dependencies": step.dependencies,
        "result_summary": step.result_summary,
        "created_at": _dt(step.created_at),
        "updated_at": _dt(step.updated_at),
    }


def _task_message_payload(message: TaskMessage) -> dict[str, object]:
    return {
        "id": str(message.id),
        "workspace_id": str(message.workspace_id),
        "task_id": str(message.task_id),
        "task_step_id": _str_or_none(message.task_step_id),
        "agent_run_id": _str_or_none(message.agent_run_id),
        "agent_profile_id": _str_or_none(message.agent_profile_id),
        "message_type": message.message_type,
        "sequence": message.sequence,
        "body": message.body,
        "payload": message.payload,
        "created_at": _dt(message.created_at),
        "updated_at": _dt(message.updated_at),
    }


def _run_payload(run: AgentRun) -> dict[str, object]:
    return {
        "id": str(run.id),
        "workspace_id": str(run.workspace_id),
        "task_id": _str_or_none(run.task_id),
        "task_step_id": _str_or_none(run.task_step_id),
        "agent_profile_id": _str_or_none(run.agent_profile_id),
        "runtime_id": _str_or_none(run.runtime_id),
        "runtime_space_id": _str_or_none(run.runtime_space_id),
        "status": run.status,
        "input": run.input,
        "output": run.output,
        "error": run.error,
        "model": run.model,
        "started_at": _dt_or_none(run.started_at),
        "completed_at": _dt_or_none(run.completed_at),
        "created_at": _dt(run.created_at),
        "updated_at": _dt(run.updated_at),
    }


def _run_event_payload(event: RunEvent) -> dict[str, object]:
    return {
        "id": str(event.id),
        "workspace_id": str(event.workspace_id),
        "agent_run_id": str(event.agent_run_id),
        "event_type": event.event_type,
        "sequence": event.sequence,
        "message": event.message,
        "metadata": event.event_metadata,
        "created_at": _dt(event.created_at),
    }


def _file_payload(file: WorkspaceFile) -> dict[str, object]:
    return {
        "id": str(file.id),
        "workspace_id": str(file.workspace_id),
        "uploaded_by_user_id": _str_or_none(file.uploaded_by_user_id),
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": file.size_bytes,
        "checksum_sha256": file.checksum_sha256,
        "status": file.status,
        "metadata": file.file_metadata,
        "created_at": _dt(file.created_at),
        "updated_at": _dt(file.updated_at),
    }


def _artifact_payload(artifact: Artifact) -> dict[str, object]:
    return {
        "id": str(artifact.id),
        "workspace_id": str(artifact.workspace_id),
        "task_id": _str_or_none(artifact.task_id),
        "agent_run_id": _str_or_none(artifact.agent_run_id),
        "task_step_id": _str_or_none(artifact.task_step_id),
        "agent_profile_id": _str_or_none(artifact.agent_profile_id),
        "supersedes_artifact_id": _str_or_none(artifact.supersedes_artifact_id),
        "work_package_id": artifact.work_package_id,
        "version": artifact.version,
        "review_status": artifact.review_status,
        "artifact_type": artifact.artifact_type,
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "checksum_sha256": artifact.checksum_sha256,
        "metadata": artifact.artifact_metadata,
        "created_at": _dt(artifact.created_at),
    }


def _runtime_space_payload(runtime_space: RuntimeSpace) -> dict[str, object]:
    return {
        "id": str(runtime_space.id),
        "workspace_id": str(runtime_space.workspace_id),
        "created_by_user_id": _str_or_none(runtime_space.created_by_user_id),
        "default_runtime_template_id": _str_or_none(runtime_space.default_runtime_template_id),
        "name": runtime_space.name,
        "scope": runtime_space.scope,
        "status": runtime_space.status,
        "policy": runtime_space.policy,
        "network_policy": runtime_space.network_policy,
        "storage_policy": runtime_space.storage_policy,
        "cleanup_policy": runtime_space.cleanup_policy,
        "created_at": _dt(runtime_space.created_at),
        "updated_at": _dt(runtime_space.updated_at),
    }


def _runtime_space_quota_payload(quota: RuntimeSpaceQuota) -> dict[str, object]:
    return {
        "id": str(quota.id),
        "workspace_id": str(quota.workspace_id),
        "runtime_space_id": str(quota.runtime_space_id),
        "quota_key": quota.quota_key,
        "limit_value": quota.limit_value,
        "reserved_value": quota.reserved_value,
        "unit": quota.unit,
        "status": quota.status,
        "created_at": _dt(quota.created_at),
        "updated_at": _dt(quota.updated_at),
    }


def _skill_install_payload(install: WorkspaceSkillInstall) -> dict[str, object]:
    return {
        "id": str(install.id),
        "workspace_id": str(install.workspace_id),
        "skill_id": str(install.skill_id),
        "installed_by_user_id": _str_or_none(install.installed_by_user_id),
        "installed_key": install.installed_key,
        "installed_name": install.installed_name,
        "installed_version": install.installed_version,
        "installed_description": install.installed_description,
        "installed_capability_keys": install.installed_capability_keys,
        "installed_manifest": install.installed_manifest,
        "source_owner_workspace_id": _str_or_none(install.source_owner_workspace_id),
        "source_visibility": install.source_visibility,
        "source_checksum": install.source_checksum,
        "config": install.config,
        "status": install.status,
        "created_at": _dt(install.created_at),
        "updated_at": _dt(install.updated_at),
    }


def _audit_payload(event: AuditEvent) -> dict[str, object]:
    return {
        "id": str(event.id),
        "workspace_id": str(event.workspace_id),
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "user_id": _str_or_none(event.user_id),
        "agent_run_id": _str_or_none(event.agent_run_id),
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "metadata": event.audit_metadata,
        "created_at": _dt(event.created_at),
    }


def _dt(value: datetime) -> str:
    return value.isoformat()


def _dt_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _str_or_none(value: object | None) -> str | None:
    return str(value) if value is not None else None


def _string_field(item: dict[str, object], key: str, default: str = "") -> str:
    value = item.get(key, default)
    return value if isinstance(value, str) else default


def _optional_string_field(item: dict[str, object], key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) else None


def _dict_field(item: dict[str, object], key: str) -> dict[str, object]:
    value = item.get(key)
    return value if isinstance(value, dict) else {}


def _remap_agent_skills(
    skills: dict[str, object],
    skill_install_id_map: dict[str, str],
) -> dict[str, object]:
    if not skill_install_id_map:
        return skills
    remapped = dict(skills)
    for key in ("installed_skill_ids", "skill_install_ids"):
        values = remapped.get(key)
        if not isinstance(values, list):
            continue
        remapped[key] = [
            skill_install_id_map.get(value, value) if isinstance(value, str) else value
            for value in values
        ]
    return remapped


def _optional_dict_field(item: dict[str, object], key: str) -> dict[str, object] | None:
    value = item.get(key)
    return value if isinstance(value, dict) else None


def _string_list_field(item: dict[str, object], key: str) -> list[str]:
    value = item.get(key)
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]


def _int_field(item: dict[str, object], key: str, default: int) -> int:
    value = item.get(key, default)
    return value if isinstance(value, int) else default


def _int_from_optional_string(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _bool_field(item: dict[str, object], key: str, default: bool) -> bool:
    value = item.get(key, default)
    return value if isinstance(value, bool) else default


def _uuid_or_none(value: str | None) -> UUID | None:
    if not value:
        return None
    return UUID(value)


def _is_valid_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _validated_checksum(
    *,
    content: bytes,
    source_checksum: str,
    source_id: str,
    collection: str,
    response: WorkspaceImportResponse,
    allow_replace: bool = False,
) -> _ChecksumResult:
    actual_checksum = sha256(content).hexdigest()
    matched = not source_checksum or source_checksum == actual_checksum
    if not matched and allow_replace:
        response.warnings.append(
            f"Imported {collection[:-1]} {source_id}: checksum replaced by resolution"
        )
        return _ChecksumResult(checksum_sha256=actual_checksum, matched=True)
    if not matched:
        response.warnings.append(
            f"Skipped {collection[:-1]} {source_id}: checksum mismatch"
        )
        response.conflict_plan.append(
            _checksum_conflict(
                collection=collection,
                source_id=source_id,
                source_checksum=source_checksum,
                actual_checksum=actual_checksum,
            )
        )
    return _ChecksumResult(checksum_sha256=actual_checksum, matched=matched)
