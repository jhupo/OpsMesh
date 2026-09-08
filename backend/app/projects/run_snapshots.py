from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.files.models import WorkspaceFile
from backend.app.projects.models import (
    AgentRunProjectSnapshot,
    WorkspaceProject,
    WorkspaceProjectConfigurationVersion,
    WorkspaceProjectFile,
    WorkspaceProjectOutput,
)
from backend.app.projects.serialization import sha256_json
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task

PROJECT_SNAPSHOT_SCHEMA_VERSION = 1


class RunProjectSnapshotService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def freeze_for_run(
        self,
        *,
        run: AgentRun,
        task: Task,
    ) -> AgentRunProjectSnapshot | None:
        self._validate_run_task_scope(run, task)
        existing = self.get_for_run(run.workspace_id, run.id)
        if existing is not None:
            return existing
        if task.workspace_project_id is None:
            return None

        project = self._session.scalar(
            select(WorkspaceProject)
            .where(
                WorkspaceProject.workspace_id == run.workspace_id,
                WorkspaceProject.id == task.workspace_project_id,
                WorkspaceProject.status == "active",
            )
            .with_for_update()
        )
        if project is None:
            raise ValueError("Active workspace project not found for run snapshot")
        configuration_version = self._session.scalar(
            select(WorkspaceProjectConfigurationVersion).where(
                WorkspaceProjectConfigurationVersion.workspace_id == run.workspace_id,
                WorkspaceProjectConfigurationVersion.project_id == project.id,
                WorkspaceProjectConfigurationVersion.version == project.configuration_version,
            )
        )
        if configuration_version is None:
            raise ValueError("Current workspace project configuration version is missing")

        manifest = self._build_manifest(project, configuration_version)
        snapshot = AgentRunProjectSnapshot(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            project_id=project.id,
            configuration_version_id=configuration_version.id,
            schema_version=PROJECT_SNAPSHOT_SCHEMA_VERSION,
            manifest=manifest,
            fingerprint_sha256=sha256_json(manifest),
        )
        self._session.add(snapshot)
        self._session.flush([snapshot])
        self._bind_snapshot_to_run_input(run, snapshot)
        return snapshot

    def clone_for_retry(
        self,
        *,
        source_run: AgentRun,
        retry_run: AgentRun,
    ) -> AgentRunProjectSnapshot | None:
        if source_run.workspace_id != retry_run.workspace_id:
            raise ValueError("Run project snapshot workspace mismatch")
        source = self.get_for_run(source_run.workspace_id, source_run.id)
        if source is None:
            return None
        snapshot = AgentRunProjectSnapshot(
            workspace_id=retry_run.workspace_id,
            agent_run_id=retry_run.id,
            project_id=source.project_id,
            configuration_version_id=source.configuration_version_id,
            schema_version=source.schema_version,
            manifest=deepcopy(source.manifest),
            fingerprint_sha256=source.fingerprint_sha256,
        )
        self._session.add(snapshot)
        self._session.flush([snapshot])
        self._bind_snapshot_to_run_input(retry_run, snapshot)
        return snapshot

    def get_for_run(
        self, workspace_id: UUID, run_id: UUID
    ) -> AgentRunProjectSnapshot | None:
        return self._session.scalar(
            select(AgentRunProjectSnapshot).where(
                AgentRunProjectSnapshot.workspace_id == workspace_id,
                AgentRunProjectSnapshot.agent_run_id == run_id,
            )
        )

    def _build_manifest(
        self,
        project: WorkspaceProject,
        configuration_version: WorkspaceProjectConfigurationVersion,
    ) -> dict[str, object]:
        file_rows = self._session.execute(
            select(WorkspaceProjectFile, WorkspaceFile)
            .join(WorkspaceFile, WorkspaceFile.id == WorkspaceProjectFile.workspace_file_id)
            .where(
                WorkspaceProjectFile.workspace_id == project.workspace_id,
                WorkspaceProjectFile.project_id == project.id,
                WorkspaceProjectFile.status == "active",
                WorkspaceFile.workspace_id == project.workspace_id,
                WorkspaceFile.status == "active",
            )
            .with_for_update()
            .order_by(WorkspaceProjectFile.project_path.asc())
        ).all()
        active_binding_count = self._session.scalar(
            select(func.count(WorkspaceProjectFile.id)).where(
                WorkspaceProjectFile.workspace_id == project.workspace_id,
                WorkspaceProjectFile.project_id == project.id,
                WorkspaceProjectFile.status == "active",
            )
        ) or 0
        if len(file_rows) != active_binding_count:
            raise ValueError("Project contains an unavailable workspace input file")
        outputs = list(
            self._session.scalars(
                select(WorkspaceProjectOutput)
                .where(
                    WorkspaceProjectOutput.workspace_id == project.workspace_id,
                    WorkspaceProjectOutput.project_id == project.id,
                    WorkspaceProjectOutput.status == "active",
                )
                .order_by(WorkspaceProjectOutput.project_path.asc())
            )
        )
        return {
            "schema_version": PROJECT_SNAPSHOT_SCHEMA_VERSION,
            "project": {
                "id": str(project.id),
                "slug": project.slug,
                "input_path": project.input_path,
                "work_path": project.work_path,
                "output_path": project.output_path,
            },
            "configuration": {
                "id": str(configuration_version.id),
                "version": configuration_version.version,
                "checksum_sha256": configuration_version.checksum_sha256,
                "value": deepcopy(configuration_version.configuration),
            },
            "files": [
                {
                    "project_file_id": str(binding.id),
                    "version": binding.version,
                    "workspace_file_id": str(workspace_file.id),
                    "project_path": binding.project_path,
                    "access_mode": binding.access_mode,
                    "filename": workspace_file.filename,
                    "content_type": workspace_file.content_type,
                    "size_bytes": workspace_file.size_bytes,
                    "checksum_sha256": workspace_file.checksum_sha256,
                    "storage_key": workspace_file.storage_key,
                }
                for binding, workspace_file in file_rows
            ],
            "outputs": [
                {
                    "project_output_id": str(output.id),
                    "project_path": output.project_path,
                    "artifact_type": output.artifact_type,
                    "content_type": output.content_type,
                    "required": output.required,
                    "max_bytes": output.max_bytes,
                }
                for output in outputs
            ],
        }

    @staticmethod
    def _validate_run_task_scope(run: AgentRun, task: Task) -> None:
        if run.workspace_id != task.workspace_id or run.task_id != task.id:
            raise ValueError("Run and task scope do not match")

    @staticmethod
    def _bind_snapshot_to_run_input(
        run: AgentRun,
        snapshot: AgentRunProjectSnapshot,
    ) -> None:
        run.input = {
            **(run.input or {}),
            "project_snapshot": {
                "id": str(snapshot.id),
                "project_id": str(snapshot.project_id),
                "schema_version": snapshot.schema_version,
                "fingerprint_sha256": snapshot.fingerprint_sha256,
            },
        }
