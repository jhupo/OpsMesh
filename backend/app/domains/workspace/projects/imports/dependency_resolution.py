from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.workspace.projects.exports.contracts import (
    WorkspaceArchiveImportRequest,
    WorkspaceImportRequest,
)
from backend.app.domains.workspace.projects.imports.fields import _is_valid_uuid
from backend.app.domains.workspace.projects.imports.resolution import _resolution


def resolved_dependency_id(
    session: Session,
    *,
    workspace_id: UUID,
    request: WorkspaceImportRequest | WorkspaceArchiveImportRequest,
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
    exists = session.scalar(
        select(model.id).where(
            model.workspace_id == workspace_id,
            model.id == UUID(target_id),
        )
    )
    return target_id if exists is not None else None


def remap_task_step_dependencies(
    dependencies: dict[str, object],
    id_map: dict[str, str],
) -> dict[str, object]:
    """Rewrite exported step references to their imported IDs.

    Dependency metadata is user-authored JSON, so unknown fields are retained. The
    scheduler only treats ``after_step_ids`` as relational data; unresolved source
    IDs are omitted instead of leaking stale UUIDs from the source workspace.
    """

    remapped = dict(dependencies)
    raw_after_step_ids = dependencies.get("after_step_ids")
    if not isinstance(raw_after_step_ids, list):
        if raw_after_step_ids is not None:
            remapped["after_step_ids"] = []
        return remapped
    remapped["after_step_ids"] = [
        mapped_id
        for source_id in raw_after_step_ids
        if isinstance(source_id, str)
        for mapped_id in [id_map.get(source_id)]
        if mapped_id is not None
    ]
    return remapped
