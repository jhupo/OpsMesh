from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.workspace.data_transfer.contracts import WorkspaceImportConflict
from backend.app.domains.workspace.data_transfer.importers.context import (
    WorkspaceMetadataImportContext,
    _dict_field,
    _int_field,
    _optional_string_field,
    _string_field,
)
from backend.app.domains.workspace.projects.models import (
    WorkspaceProject,
    WorkspaceProjectConfigurationVersion,
    WorkspaceProjectOutput,
)
from backend.app.domains.workspace.projects.policy import (
    normalize_project_description,
    normalize_project_name,
    normalize_project_slug,
    require_path_within,
    validate_output_declaration,
    validate_project_configuration,
)


class WorkspaceProjectMetadataImporter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def import_projects(self, context: WorkspaceMetadataImportContext) -> None:
        if not context.request.import_projects:
            return
        for item in context.request.export.projects[: context.request.max_items_per_collection]:
            source_id = _string_field(item, "id")
            try:
                name = normalize_project_name(
                    f"{context.request.name_prefix}"
                    f"{_string_field(item, 'name', 'Imported project')}"
                )
                slug = normalize_project_slug(
                    f"imported-{_string_field(item, 'slug', source_id[:8])}"
                )
                configuration = validate_project_configuration(_dict_field(item, "configuration"))
            except ValueError as exc:
                context.skipped_counts["projects"] += 1
                context.conflict_plan.append(
                    self._conflict("projects", source_id, str(exc))
                )
                continue
            if self._session.scalar(
                select(WorkspaceProject.id).where(
                    WorkspaceProject.workspace_id == context.workspace.id,
                    WorkspaceProject.slug == slug,
                )
            ) is not None:
                context.skipped_counts["projects"] += 1
                context.conflict_plan.append(
                    self._conflict(
                        "projects",
                        source_id,
                        f"Project slug {slug!r} already exists in the target workspace",
                    )
                )
                continue
            project = WorkspaceProject(
                id=uuid4(),
                workspace_id=context.workspace.id,
                created_by_user_id=context.user_id,
                name=name,
                slug=slug,
                description=normalize_project_description(
                    _string_field(item, "description")
                ),
                input_path=_string_field(item, "input_path", "inputs"),
                work_path=_string_field(item, "work_path", "work"),
                output_path=_string_field(item, "output_path", "outputs"),
                configuration=configuration,
                configuration_version=max(_int_field(item, "configuration_version", 1), 1),
                status=_string_field(item, "status", "active"),
            )
            self._session.add(project)
            self._session.flush()
            context.id_map["projects"][source_id] = str(project.id)
            context.created_counts["projects"] += 1

        for item in context.request.export.project_configuration_versions[
            : context.request.max_items_per_collection
        ]:
            source_project_id = _string_field(item, "project_id")
            project_id = context.id_map["projects"].get(source_project_id)
            if project_id is None:
                continue
            configuration = validate_project_configuration(_dict_field(item, "configuration"))
            self._session.add(
                WorkspaceProjectConfigurationVersion(
                    id=uuid4(),
                    workspace_id=context.workspace.id,
                    project_id=UUID(project_id),
                    version=max(_int_field(item, "version", 1), 1),
                    configuration=configuration,
                    checksum_sha256=_string_field(item, "checksum_sha256"),
                    created_by_user_id=context.user_id,
                    change_summary=_string_field(item, "change_summary"),
                )
            )
            context.created_counts["project_configuration_versions"] += 1

        for item in context.request.export.project_outputs[
            : context.request.max_items_per_collection
        ]:
            project_id = context.id_map["projects"].get(_string_field(item, "project_id"))
            if project_id is None:
                continue
            try:
                target_project = self._session.get(WorkspaceProject, UUID(project_id))
                if target_project is None:
                    raise ValueError("Imported project is missing")
                project_path = require_path_within(
                    _string_field(item, "project_path", "outputs/result"),
                    target_project.output_path,
                    kind="output",
                )
                artifact_type, content_type, max_bytes = validate_output_declaration(
                    artifact_type=_string_field(item, "artifact_type"),
                    content_type=_optional_string_field(item, "content_type"),
                    max_bytes=max(_int_field(item, "max_bytes", 1), 1),
                )
            except ValueError as exc:
                context.skipped_counts["project_outputs"] += 1
                context.conflict_plan.append(
                    self._conflict("project_outputs", _string_field(item, "id"), str(exc))
                )
                continue
            self._session.add(
                WorkspaceProjectOutput(
                    id=uuid4(),
                    workspace_id=context.workspace.id,
                    project_id=UUID(project_id),
                    project_path=project_path,
                    artifact_type=artifact_type,
                    content_type=content_type,
                    required=item.get("required") is not False,
                    max_bytes=max_bytes,
                    status=_string_field(item, "status", "active"),
                )
            )
            context.created_counts["project_outputs"] += 1

    @staticmethod
    def _conflict(
        collection: str, source_id: str, message: str
    ) -> WorkspaceImportConflict:
        return WorkspaceImportConflict(
            collection=collection,
            source_id=source_id,
            strategy="reject",
            severity="error",
            message=message,
        )
