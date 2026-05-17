import json
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from typing import Any
from uuid import UUID
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
    WorkspaceImportRequest,
    WorkspaceImportResponse,
)
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.files.models import WorkspaceFile
from backend.app.files.security import safe_filename
from backend.app.files.storage import LocalStorage
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workspaces.models import Workspace


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
            "runs": [],
            "run_events": [],
            "files": [],
            "artifacts": [],
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
                format_version="workspace-export.v1",
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
        )

    def import_metadata(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceImportRequest,
    ) -> WorkspaceImportResponse:
        id_map: dict[str, dict[str, str]] = {
            "agents": {},
            "teams": {},
            "tasks": {},
        }
        created_counts = {"agents": 0, "teams": 0, "team_members": 0, "tasks": 0, "task_steps": 0}
        skipped_counts = {"agents": 0, "teams": 0, "team_members": 0, "tasks": 0, "task_steps": 0}
        warnings: list[str] = []

        if request.import_agents:
            for item in request.export.agents[: request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                imported_name = f"{request.name_prefix}{_string_field(item, 'name')}"
                if self._agent_exists(workspace.id, imported_name):
                    skipped_counts["agents"] += 1
                    continue
                created_counts["agents"] += 1
                if request.dry_run:
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
                    skills=_dict_field(item, "skills"),
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
                imported_name = f"{request.name_prefix}{_string_field(item, 'name')}"
                if self._team_exists(workspace.id, imported_name):
                    skipped_counts["teams"] += 1
                    continue
                created_counts["teams"] += 1
                if request.dry_run:
                    continue
                manager_id = id_map["agents"].get(_string_field(item, "manager_agent_profile_id"))
                team = AgentTeam(
                    workspace_id=workspace.id,
                    name=imported_name,
                    team_type=_string_field(item, "team_type", "general"),
                    description=_string_field(item, "description"),
                    manager_agent_profile_id=_uuid_or_none(manager_id),
                    coordination_rules=_dict_field(item, "coordination_rules"),
                    default_task_policy=_dict_field(item, "default_task_policy"),
                    status="active",
                )
                self._session.add(team)
                self._session.flush()
                id_map["teams"][source_id] = str(team.id)

            for item in request.export.team_members[: request.max_items_per_collection]:
                team_id = id_map["teams"].get(_string_field(item, "agent_team_id"))
                agent_id = id_map["agents"].get(_string_field(item, "agent_profile_id"))
                if team_id is None or agent_id is None:
                    skipped_counts["team_members"] += 1
                    warnings.append("Skipped team member with missing imported team or agent")
                    continue
                created_counts["team_members"] += 1
                if request.dry_run:
                    continue
                self._session.add(
                    AgentTeamMember(
                        workspace_id=workspace.id,
                        agent_team_id=UUID(team_id),
                        agent_profile_id=UUID(agent_id),
                        team_role=_string_field(item, "team_role"),
                        is_required=_bool_field(item, "is_required", True),
                        order_index=_int_field(item, "order_index", 0),
                    )
                )

        if request.import_tasks:
            for item in request.export.tasks[: request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                imported_title = f"{request.name_prefix}{_string_field(item, 'title')}"
                if self._task_exists(workspace.id, imported_title):
                    skipped_counts["tasks"] += 1
                    continue
                created_counts["tasks"] += 1
                if request.dry_run:
                    continue
                team_id = id_map["teams"].get(_string_field(item, "agent_team_id"))
                task = Task(
                    workspace_id=workspace.id,
                    created_by_user_id=user_id,
                    agent_team_id=_uuid_or_none(team_id),
                    domain_type=_string_field(item, "domain_type", "general"),
                    title=imported_title,
                    description=_string_field(item, "description"),
                    status="draft",
                    priority=_int_field(item, "priority", 0),
                    input=_dict_field(item, "input"),
                    generic_state=_dict_field(item, "generic_state"),
                    domain_state=_dict_field(item, "domain_state"),
                    final_output=_optional_dict_field(item, "final_output"),
                )
                self._session.add(task)
                self._session.flush()
                id_map["tasks"][source_id] = str(task.id)

            for item in request.export.task_steps[: request.max_items_per_collection]:
                task_id = id_map["tasks"].get(_string_field(item, "task_id"))
                if task_id is None:
                    skipped_counts["task_steps"] += 1
                    warnings.append("Skipped task step with missing imported task")
                    continue
                created_counts["task_steps"] += 1
                if request.dry_run:
                    continue
                agent_id = id_map["agents"].get(_string_field(item, "assigned_agent_profile_id"))
                self._session.add(
                    TaskStep(
                        workspace_id=workspace.id,
                        task_id=UUID(task_id),
                        assigned_agent_profile_id=_uuid_or_none(agent_id),
                        title=_string_field(item, "title"),
                        description=_string_field(item, "description"),
                        status="queued",
                        order_index=_int_field(item, "order_index", 0),
                        dependencies=_dict_field(item, "dependencies"),
                        result_summary=_optional_string_field(item, "result_summary"),
                    )
                )

        response = WorkspaceImportResponse(
            dry_run=request.dry_run,
            source_workspace_id=request.export.manifest.workspace_id,
            target_workspace_id=workspace.id,
            created_counts=created_counts,
            skipped_counts=skipped_counts,
            id_map=id_map,
            warnings=warnings,
        )
        if request.dry_run:
            self._session.rollback()
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
                    name_prefix=request.name_prefix,
                    max_items_per_collection=request.max_items_per_collection,
                ),
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
            if request.dry_run:
                self._session.rollback()
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
        response.created_counts["files"] += 1
        total_bytes += len(content)
        if request.dry_run:
            return total_bytes
        checksum = _validated_checksum(
            content=content,
            source_checksum=_string_field(item, "checksum_sha256"),
            source_id=source_id,
            collection="file",
            warnings=response.warnings,
        )
        imported_filename = safe_filename(f"{request.name_prefix}{filename}")
        file = WorkspaceFile(
            workspace_id=workspace.id,
            uploaded_by_user_id=user_id,
            filename=imported_filename,
            content_type=_string_field(item, "content_type", "application/octet-stream"),
            size_bytes=len(content),
            checksum_sha256=checksum,
            storage_key=(
                f"workspaces/{workspace.id}/files/imported/{source_id}/{imported_filename}"
            ),
            status="active",
            file_metadata={
                **_dict_field(item, "metadata"),
                "imported_from_file_id": source_id,
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
        response.created_counts["artifacts"] += 1
        total_bytes += len(content)
        if request.dry_run:
            return total_bytes
        source_task_id = _string_field(item, "task_id")
        imported_task_id = response.id_map["tasks"].get(source_task_id)
        if source_task_id and imported_task_id is None:
            response.warnings.append(
                f"Imported artifact {source_id} without a mapped task"
            )
        checksum = _validated_checksum(
            content=content,
            source_checksum=_string_field(item, "checksum_sha256"),
            source_id=source_id,
            collection="artifact",
            warnings=response.warnings,
        )
        imported_filename = safe_filename(f"{request.name_prefix}{filename}")
        artifact = Artifact(
            workspace_id=workspace.id,
            task_id=_uuid_or_none(imported_task_id),
            agent_run_id=None,
            artifact_type=_string_field(item, "artifact_type", "file"),
            filename=imported_filename,
            content_type=_string_field(item, "content_type", "application/octet-stream"),
            size_bytes=len(content),
            checksum_sha256=checksum,
            storage_key=(
                f"workspaces/{workspace.id}/artifacts/imported/{source_id}/"
                f"{imported_filename}"
            ),
            artifact_metadata={
                **_dict_field(item, "metadata"),
                "imported_from_artifact_id": source_id,
                "source_task_id": source_task_id or None,
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
            return None
        content = archive.read(archive_name)
        if len(content) > request.max_bytes_per_object:
            response.skipped_counts[collection] += 1
            response.warnings.append(f"Skipped {collection[:-1]} {source_id}: object too large")
            return None
        if total_bytes + len(content) > request.max_total_bytes:
            response.skipped_counts[collection] += 1
            response.warnings.append(
                f"Skipped {collection[:-1]} {source_id}: archive byte limit reached"
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
        "team_role": member.team_role,
        "is_required": member.is_required,
        "order_index": member.order_index,
    }


def _task_payload(task: Task) -> dict[str, object]:
    return {
        "id": str(task.id),
        "workspace_id": str(task.workspace_id),
        "created_by_user_id": _str_or_none(task.created_by_user_id),
        "created_by_agent_run_id": _str_or_none(task.created_by_agent_run_id),
        "agent_team_id": _str_or_none(task.agent_team_id),
        "domain_type": task.domain_type,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "input": task.input,
        "generic_state": task.generic_state,
        "domain_state": task.domain_state,
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
        "title": step.title,
        "description": step.description,
        "status": step.status,
        "order_index": step.order_index,
        "dependencies": step.dependencies,
        "result_summary": step.result_summary,
        "created_at": _dt(step.created_at),
        "updated_at": _dt(step.updated_at),
    }


def _run_payload(run: AgentRun) -> dict[str, object]:
    return {
        "id": str(run.id),
        "workspace_id": str(run.workspace_id),
        "task_id": _str_or_none(run.task_id),
        "task_step_id": _str_or_none(run.task_step_id),
        "agent_profile_id": _str_or_none(run.agent_profile_id),
        "runtime_id": _str_or_none(run.runtime_id),
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
        "storage_key": file.storage_key,
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
        "artifact_type": artifact.artifact_type,
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "checksum_sha256": artifact.checksum_sha256,
        "storage_key": artifact.storage_key,
        "metadata": artifact.artifact_metadata,
        "created_at": _dt(artifact.created_at),
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


def _optional_dict_field(item: dict[str, object], key: str) -> dict[str, object] | None:
    value = item.get(key)
    return value if isinstance(value, dict) else None


def _int_field(item: dict[str, object], key: str, default: int) -> int:
    value = item.get(key, default)
    return value if isinstance(value, int) else default


def _bool_field(item: dict[str, object], key: str, default: bool) -> bool:
    value = item.get(key, default)
    return value if isinstance(value, bool) else default


def _uuid_or_none(value: str | None) -> UUID | None:
    if not value:
        return None
    return UUID(value)


def _validated_checksum(
    *,
    content: bytes,
    source_checksum: str,
    source_id: str,
    collection: str,
    warnings: list[str],
) -> str:
    actual_checksum = sha256(content).hexdigest()
    if source_checksum and source_checksum != actual_checksum:
        warnings.append(f"Imported {collection} {source_id} with checksum mismatch")
    return actual_checksum
