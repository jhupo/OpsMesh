from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.files.models import WorkspaceFile
from backend.app.projects.diffs import ProjectDiffEntry, diff_json
from backend.app.projects.models import (
    WorkspaceProject,
    WorkspaceProjectConfigurationVersion,
    WorkspaceProjectFile,
)
from backend.app.projects.policy import normalize_project_path


@dataclass(frozen=True, slots=True)
class ProjectFileVersionView:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    workspace_file_id: UUID
    supersedes_project_file_id: UUID | None
    project_path: str
    version: int
    access_mode: str
    status: str
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    created_at: datetime
    updated_at: datetime

    def diff_value(self) -> dict[str, object]:
        return {
            "access_mode": self.access_mode,
            "checksum_sha256": self.checksum_sha256,
            "content_type": self.content_type,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "workspace_file_id": str(self.workspace_file_id),
        }


class WorkspaceProjectVersionQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_configuration_versions(
        self, workspace_id: UUID, project_id: UUID
    ) -> list[WorkspaceProjectConfigurationVersion] | None:
        if not self._project_exists(workspace_id, project_id):
            return None
        return list(
            self._session.scalars(
                select(WorkspaceProjectConfigurationVersion)
                .where(
                    WorkspaceProjectConfigurationVersion.workspace_id == workspace_id,
                    WorkspaceProjectConfigurationVersion.project_id == project_id,
                )
                .order_by(WorkspaceProjectConfigurationVersion.version.desc())
            )
        )

    def configuration_diff(
        self,
        workspace_id: UUID,
        project_id: UUID,
        *,
        from_version: int,
        to_version: int,
    ) -> list[ProjectDiffEntry] | None:
        if not self._project_exists(workspace_id, project_id):
            return None
        versions = list(
            self._session.scalars(
                select(WorkspaceProjectConfigurationVersion).where(
                    WorkspaceProjectConfigurationVersion.workspace_id == workspace_id,
                    WorkspaceProjectConfigurationVersion.project_id == project_id,
                    WorkspaceProjectConfigurationVersion.version.in_((from_version, to_version)),
                )
            )
        )
        by_version = {item.version: item for item in versions}
        if from_version not in by_version or to_version not in by_version:
            raise ValueError("Project configuration version not found")
        return diff_json(
            by_version[from_version].configuration,
            by_version[to_version].configuration,
        )

    def list_file_versions(
        self,
        workspace_id: UUID,
        project_id: UUID,
        *,
        project_path: str,
    ) -> list[ProjectFileVersionView] | None:
        if not self._project_exists(workspace_id, project_id):
            return None
        normalized_path = normalize_project_path(project_path)
        rows = self._session.execute(
            select(WorkspaceProjectFile, WorkspaceFile)
            .join(WorkspaceFile, WorkspaceFile.id == WorkspaceProjectFile.workspace_file_id)
            .where(
                WorkspaceProjectFile.workspace_id == workspace_id,
                WorkspaceProjectFile.project_id == project_id,
                WorkspaceProjectFile.project_path == normalized_path,
                WorkspaceFile.workspace_id == workspace_id,
            )
            .order_by(WorkspaceProjectFile.version.desc())
        ).all()
        return [_file_version_view(binding, workspace_file) for binding, workspace_file in rows]

    def file_diff(
        self,
        workspace_id: UUID,
        project_id: UUID,
        *,
        project_path: str,
        from_version: int,
        to_version: int,
    ) -> list[ProjectDiffEntry] | None:
        versions = self.list_file_versions(
            workspace_id,
            project_id,
            project_path=project_path,
        )
        if versions is None:
            return None
        by_version = {item.version: item for item in versions}
        if from_version not in by_version or to_version not in by_version:
            raise ValueError("Project file version not found")
        return diff_json(
            by_version[from_version].diff_value(),
            by_version[to_version].diff_value(),
        )

    def _project_exists(self, workspace_id: UUID, project_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(WorkspaceProject.id).where(
                    WorkspaceProject.workspace_id == workspace_id,
                    WorkspaceProject.id == project_id,
                )
            )
            is not None
        )


def _file_version_view(
    binding: WorkspaceProjectFile,
    workspace_file: WorkspaceFile,
) -> ProjectFileVersionView:
    return ProjectFileVersionView(
        id=binding.id,
        workspace_id=binding.workspace_id,
        project_id=binding.project_id,
        workspace_file_id=binding.workspace_file_id,
        supersedes_project_file_id=binding.supersedes_project_file_id,
        project_path=binding.project_path,
        version=binding.version,
        access_mode=binding.access_mode,
        status=binding.status,
        filename=workspace_file.filename,
        content_type=workspace_file.content_type,
        size_bytes=workspace_file.size_bytes,
        checksum_sha256=workspace_file.checksum_sha256,
        created_at=binding.created_at,
        updated_at=binding.updated_at,
    )
