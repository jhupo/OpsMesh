from dataclasses import dataclass
from uuid import UUID

from backend.app.domains.workspace.projects.exports.contracts import (
    WorkspaceImportConflict,
    WorkspaceImportRequest,
)
from backend.app.domains.workspace.tenants.models import Workspace


@dataclass(slots=True)
class WorkspaceMetadataImportContext:
    workspace: Workspace
    user_id: UUID
    request: WorkspaceImportRequest
    id_map: dict[str, dict[str, str]]
    created_counts: dict[str, int]
    skipped_counts: dict[str, int]
    warnings: list[str]
    conflict_plan: list[WorkspaceImportConflict]
