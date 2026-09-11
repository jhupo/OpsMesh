from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.observability.audit_service import AuditService
from backend.app.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.db.pagination import page_scalars_by_offset
from backend.app.files.models import WorkspaceFile
from backend.app.projects.contracts import (
    ProjectCreateCommand,
    ProjectFileCommand,
    ProjectFileReplacementCommand,
    ProjectOutputCommand,
    ProjectUpdateCommand,
)
from backend.app.projects.models import (
    WorkspaceProject,
    WorkspaceProjectConfigurationVersion,
    WorkspaceProjectFile,
    WorkspaceProjectOutput,
)
from backend.app.projects.policy import (
    normalize_change_summary,
    normalize_project_description,
    normalize_project_name,
    normalize_project_slug,
    require_path_within,
    validate_output_declaration,
    validate_project_configuration,
    validate_project_layout,
)
from backend.app.projects.serialization import sha256_json


class WorkspaceProjectService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_projects(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        offset: int,
        include_archived: bool = False,
    ) -> tuple[list[WorkspaceProject], int]:
        statement = select(WorkspaceProject).where(WorkspaceProject.workspace_id == workspace_id)
        if not include_archived:
            statement = statement.where(WorkspaceProject.status == "active")
        return page_scalars_by_offset(
            self._session,
            statement.order_by(WorkspaceProject.created_at.desc(), WorkspaceProject.id.desc()),
            limit=limit,
            offset=offset,
        )

    def get_project(
        self, workspace_id: UUID, project_id: UUID, *, include_archived: bool = False
    ) -> WorkspaceProject | None:
        statement = select(WorkspaceProject).where(
            WorkspaceProject.workspace_id == workspace_id,
            WorkspaceProject.id == project_id,
        )
        if not include_archived:
            statement = statement.where(WorkspaceProject.status == "active")
        return self._session.scalar(statement)

    def create_project(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: ProjectCreateCommand,
    ) -> WorkspaceProject:
        input_path, work_path, output_path = validate_project_layout(
            input_path=command.input_path,
            work_path=command.work_path,
            output_path=command.output_path,
        )
        configuration = validate_project_configuration(command.configuration)
        project = WorkspaceProject(
            workspace_id=workspace_id,
            created_by_user_id=actor_user_id,
            name=normalize_project_name(command.name),
            slug=normalize_project_slug(command.slug),
            description=normalize_project_description(command.description),
            input_path=input_path,
            work_path=work_path,
            output_path=output_path,
            configuration=configuration,
            configuration_version=1,
        )
        self._session.add(project)
        flush_or_raise_conflict(self._session, "Workspace project slug already exists")
        self._session.add(
            WorkspaceProjectConfigurationVersion(
                workspace_id=workspace_id,
                project_id=project.id,
                version=1,
                configuration=configuration,
                checksum_sha256=sha256_json(configuration),
                created_by_user_id=actor_user_id,
                change_summary="Initial configuration",
            )
        )
        self._session.flush()
        self._audit(project, actor_user_id, "workspace_project.created")
        commit_or_raise_conflict(self._session, "Workspace project slug already exists")
        self._session.refresh(project)
        return project

    def update_project(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        actor_user_id: UUID,
        command: ProjectUpdateCommand,
    ) -> WorkspaceProject | None:
        project = self._lock_project(workspace_id, project_id)
        if project is None:
            return None
        previous_configuration_version = project.configuration_version
        configuration_changed = False
        input_path, work_path, output_path = validate_project_layout(
            input_path=command.input_path if command.input_path is not None else project.input_path,
            work_path=command.work_path if command.work_path is not None else project.work_path,
            output_path=command.output_path
            if command.output_path is not None
            else project.output_path,
        )
        self._ensure_existing_paths_fit(project, input_path=input_path, output_path=output_path)
        if command.name is not None:
            project.name = normalize_project_name(command.name)
        if command.description is not None:
            project.description = normalize_project_description(command.description)
        project.input_path = input_path
        project.work_path = work_path
        project.output_path = output_path
        change_summary = normalize_change_summary(command.change_summary)
        if command.configuration is None and change_summary:
            raise ValueError("Change summary requires a configuration update")
        if command.configuration is not None:
            configuration = validate_project_configuration(command.configuration)
            if configuration != project.configuration:
                configuration_changed = True
                project.configuration_version += 1
                project.configuration = configuration
                self._session.add(
                    WorkspaceProjectConfigurationVersion(
                        workspace_id=workspace_id,
                        project_id=project.id,
                        version=project.configuration_version,
                        configuration=configuration,
                        checksum_sha256=sha256_json(configuration),
                        created_by_user_id=actor_user_id,
                        change_summary=change_summary,
                    )
                )
        self._audit(
            project,
            actor_user_id,
            "workspace_project.updated",
            metadata={
                "slug": project.slug,
                "configuration_changed": configuration_changed,
                "previous_configuration_version": previous_configuration_version,
                "configuration_version": project.configuration_version,
            },
        )
        self._session.commit()
        self._session.refresh(project)
        return project

    def archive_project(
        self, *, workspace_id: UUID, project_id: UUID, actor_user_id: UUID
    ) -> WorkspaceProject | None:
        project = self._lock_project(workspace_id, project_id)
        if project is None:
            return None
        project.status = "archived"
        self._audit(project, actor_user_id, "workspace_project.archived")
        self._session.commit()
        self._session.refresh(project)
        return project

    def list_input_files(
        self, workspace_id: UUID, project_id: UUID
    ) -> list[WorkspaceProjectFile] | None:
        if self.get_project(workspace_id, project_id) is None:
            return None
        return list(
            self._session.scalars(
                select(WorkspaceProjectFile)
                .where(
                    WorkspaceProjectFile.workspace_id == workspace_id,
                    WorkspaceProjectFile.project_id == project_id,
                    WorkspaceProjectFile.status == "active",
                )
                .order_by(WorkspaceProjectFile.project_path.asc())
            )
        )

    def add_input_file(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        actor_user_id: UUID,
        command: ProjectFileCommand,
    ) -> WorkspaceProjectFile | None:
        project = self._lock_project(workspace_id, project_id)
        if project is None:
            return None
        workspace_file = self._active_workspace_file(workspace_id, command.workspace_file_id)
        if workspace_file is None:
            raise ValueError("Workspace file not found")
        if command.access_mode not in {"read_only", "copy_on_write"}:
            raise ValueError("Unsupported project file access mode")
        project_path = require_path_within(command.project_path, project.input_path, kind="input")
        binding = WorkspaceProjectFile(
            workspace_id=workspace_id,
            project_id=project.id,
            workspace_file_id=workspace_file.id,
            supersedes_project_file_id=None,
            project_path=project_path,
            version=1,
            access_mode=command.access_mode,
        )
        previous = self._latest_file_version(project, project_path)
        if previous is not None:
            binding.supersedes_project_file_id = previous.id
            binding.version = previous.version + 1
        self._session.add(binding)
        flush_or_raise_conflict(self._session, "Project input file or path already exists")
        self._audit(
            project,
            actor_user_id,
            "workspace_project.input_file_added",
            metadata={
                "project_file_id": str(binding.id),
                "project_path": project_path,
                "version": binding.version,
            },
        )
        commit_or_raise_conflict(self._session, "Project input file or path already exists")
        self._session.refresh(binding)
        return binding

    def replace_input_file(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        project_file_id: UUID,
        actor_user_id: UUID,
        command: ProjectFileReplacementCommand,
    ) -> WorkspaceProjectFile | None:
        project = self._lock_project(workspace_id, project_id)
        if project is None:
            return None
        current = self._session.scalar(
            select(WorkspaceProjectFile).where(
                WorkspaceProjectFile.workspace_id == workspace_id,
                WorkspaceProjectFile.project_id == project_id,
                WorkspaceProjectFile.id == project_file_id,
                WorkspaceProjectFile.status == "active",
            )
        )
        if current is None:
            raise ValueError("Active project input file not found")
        workspace_file = self._active_workspace_file(workspace_id, command.workspace_file_id)
        if workspace_file is None:
            raise ValueError("Workspace file not found")
        access_mode = command.access_mode or current.access_mode
        if access_mode not in {"read_only", "copy_on_write"}:
            raise ValueError("Unsupported project file access mode")

        current.status = "superseded"
        self._session.flush([current])
        replacement = WorkspaceProjectFile(
            workspace_id=workspace_id,
            project_id=project.id,
            workspace_file_id=workspace_file.id,
            supersedes_project_file_id=current.id,
            project_path=current.project_path,
            version=current.version + 1,
            access_mode=access_mode,
        )
        self._session.add(replacement)
        flush_or_raise_conflict(self._session, "Project input file replacement conflicts")
        self._audit(
            project,
            actor_user_id,
            "workspace_project.input_file_replaced",
            metadata={
                "project_file_id": str(replacement.id),
                "supersedes_project_file_id": str(current.id),
                "project_path": replacement.project_path,
                "version": replacement.version,
            },
        )
        commit_or_raise_conflict(self._session, "Project input file replacement conflicts")
        self._session.refresh(replacement)
        return replacement

    def remove_input_file(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        project_file_id: UUID,
        actor_user_id: UUID,
    ) -> bool:
        project = self._lock_project(workspace_id, project_id)
        if project is None:
            return False
        binding = self._session.scalar(
            select(WorkspaceProjectFile).where(
                WorkspaceProjectFile.workspace_id == workspace_id,
                WorkspaceProjectFile.project_id == project_id,
                WorkspaceProjectFile.id == project_file_id,
                WorkspaceProjectFile.status == "active",
            )
        )
        if binding is None:
            return False
        binding.status = "removed"
        self._audit(project, actor_user_id, "workspace_project.input_file_removed")
        self._session.commit()
        return True

    def list_outputs(
        self, workspace_id: UUID, project_id: UUID
    ) -> list[WorkspaceProjectOutput] | None:
        if self.get_project(workspace_id, project_id) is None:
            return None
        return list(
            self._session.scalars(
                select(WorkspaceProjectOutput)
                .where(
                    WorkspaceProjectOutput.workspace_id == workspace_id,
                    WorkspaceProjectOutput.project_id == project_id,
                    WorkspaceProjectOutput.status == "active",
                )
                .order_by(WorkspaceProjectOutput.project_path.asc())
            )
        )

    def add_output(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        actor_user_id: UUID,
        command: ProjectOutputCommand,
    ) -> WorkspaceProjectOutput | None:
        project = self._lock_project(workspace_id, project_id)
        if project is None:
            return None
        project_path = require_path_within(command.project_path, project.output_path, kind="output")
        artifact_type, content_type, max_bytes = validate_output_declaration(
            artifact_type=command.artifact_type,
            content_type=command.content_type,
            max_bytes=command.max_bytes,
        )
        output = WorkspaceProjectOutput(
            workspace_id=workspace_id,
            project_id=project.id,
            project_path=project_path,
            artifact_type=artifact_type,
            content_type=content_type,
            required=command.required,
            max_bytes=max_bytes,
        )
        self._session.add(output)
        flush_or_raise_conflict(self._session, "Project output path already exists")
        self._audit(
            project,
            actor_user_id,
            "workspace_project.output_declared",
            metadata={"project_output_id": str(output.id), "project_path": project_path},
        )
        commit_or_raise_conflict(self._session, "Project output path already exists")
        self._session.refresh(output)
        return output

    def remove_output(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        project_output_id: UUID,
        actor_user_id: UUID,
    ) -> bool:
        project = self._lock_project(workspace_id, project_id)
        if project is None:
            return False
        output = self._session.scalar(
            select(WorkspaceProjectOutput).where(
                WorkspaceProjectOutput.workspace_id == workspace_id,
                WorkspaceProjectOutput.project_id == project_id,
                WorkspaceProjectOutput.id == project_output_id,
                WorkspaceProjectOutput.status == "active",
            )
        )
        if output is None:
            return False
        output.status = "removed"
        self._audit(project, actor_user_id, "workspace_project.output_removed")
        self._session.commit()
        return True

    def _lock_project(self, workspace_id: UUID, project_id: UUID) -> WorkspaceProject | None:
        return self._session.scalar(
            select(WorkspaceProject)
            .where(
                WorkspaceProject.workspace_id == workspace_id,
                WorkspaceProject.id == project_id,
                WorkspaceProject.status == "active",
            )
            .with_for_update()
        )

    def _active_workspace_file(
        self, workspace_id: UUID, workspace_file_id: UUID
    ) -> WorkspaceFile | None:
        return self._session.scalar(
            select(WorkspaceFile).where(
                WorkspaceFile.workspace_id == workspace_id,
                WorkspaceFile.id == workspace_file_id,
                WorkspaceFile.status == "active",
            )
        )

    def _latest_file_version(
        self, project: WorkspaceProject, project_path: str
    ) -> WorkspaceProjectFile | None:
        return self._session.scalar(
            select(WorkspaceProjectFile)
            .where(
                WorkspaceProjectFile.workspace_id == project.workspace_id,
                WorkspaceProjectFile.project_id == project.id,
                WorkspaceProjectFile.project_path == project_path,
            )
            .order_by(WorkspaceProjectFile.version.desc())
            .limit(1)
        )

    def _ensure_existing_paths_fit(
        self, project: WorkspaceProject, *, input_path: str, output_path: str
    ) -> None:
        input_paths = self._session.scalars(
            select(WorkspaceProjectFile.project_path).where(
                WorkspaceProjectFile.workspace_id == project.workspace_id,
                WorkspaceProjectFile.project_id == project.id,
                WorkspaceProjectFile.status == "active",
            )
        )
        for path in input_paths:
            require_path_within(path, input_path, kind="input")
        output_paths = self._session.scalars(
            select(WorkspaceProjectOutput.project_path).where(
                WorkspaceProjectOutput.workspace_id == project.workspace_id,
                WorkspaceProjectOutput.project_id == project.id,
                WorkspaceProjectOutput.status == "active",
            )
        )
        for path in output_paths:
            require_path_within(path, output_path, kind="output")

    def _audit(
        self,
        project: WorkspaceProject,
        actor_user_id: UUID,
        action: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=project.workspace_id,
            user_id=actor_user_id,
            action=action,
            target_type="workspace_project",
            target_id=project.id,
            metadata=metadata or {"slug": project.slug},
        )
