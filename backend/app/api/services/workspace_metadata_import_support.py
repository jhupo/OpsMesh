from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import WorkspaceImportRequest
from backend.app.api.services.workspace_import_fields import _is_valid_uuid
from backend.app.api.services.workspace_import_resolution import _resolution


def resolved_dependency_id(
    session: Session,
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
    exists = session.scalar(
        select(model.id).where(
            model.workspace_id == workspace_id,
            model.id == UUID(target_id),
        )
    )
    return target_id if exists is not None else None
