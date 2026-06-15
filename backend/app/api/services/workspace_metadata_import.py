from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceImportConflict,
    WorkspaceImportRequest,
    WorkspaceImportResponse,
)
from backend.app.api.services.workspace_agent_import import AgentMetadataImporter
from backend.app.api.services.workspace_import_conflicts import (
    _preview_token_conflict,
    _unsupported_format_conflict,
)
from backend.app.api.services.workspace_import_preview import (
    _import_preview_audit_metadata,
    _populate_import_preview,
)
from backend.app.api.services.workspace_import_tokens import _metadata_preview_token
from backend.app.api.services.workspace_metadata_import_context import (
    WorkspaceMetadataImportContext,
)
from backend.app.api.services.workspace_runtime_space_import import RuntimeSpaceMetadataImporter
from backend.app.api.services.workspace_skill_install_import import SkillInstallMetadataImporter
from backend.app.api.services.workspace_task_import import TaskMetadataImporter
from backend.app.api.services.workspace_team_import import TeamMetadataImporter
from backend.app.audit.service import AuditService
from backend.app.workspaces.models import Workspace


class WorkspaceMetadataImportService:
    def __init__(self, session: Session) -> None:
        self._session = session

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

        token_response = self._preview_token_response(
            workspace=workspace,
            request=request,
            preview_token=preview_token,
            created_counts=created_counts,
            skipped_counts=skipped_counts,
            id_map=id_map,
            conflict_plan=conflict_plan,
        )
        if token_response is not None:
            if request.dry_run and record_preview:
                self.record_import_preview(
                    workspace_id=workspace.id,
                    user_id=user_id,
                    action="workspace.import.previewed",
                    response=token_response,
                )
            return token_response

        unsupported_response = self._unsupported_format_response(
            workspace=workspace,
            request=request,
            preview_token=preview_token,
            created_counts=created_counts,
            skipped_counts=skipped_counts,
            id_map=id_map,
            conflict_plan=conflict_plan,
        )
        if unsupported_response is not None:
            if request.dry_run and record_preview:
                self.record_import_preview(
                    workspace_id=workspace.id,
                    user_id=user_id,
                    action="workspace.import.previewed",
                    response=unsupported_response,
                )
            return unsupported_response

        ctx = WorkspaceMetadataImportContext(
            workspace=workspace,
            user_id=user_id,
            request=request,
            id_map=id_map,
            created_counts=created_counts,
            skipped_counts=skipped_counts,
            warnings=warnings,
            conflict_plan=conflict_plan,
        )
        RuntimeSpaceMetadataImporter(self._session).import_runtime_spaces(ctx)
        SkillInstallMetadataImporter(self._session).import_skill_installs(ctx)
        AgentMetadataImporter(self._session).import_agents(ctx)
        TeamMetadataImporter(self._session).import_teams(ctx)
        TaskMetadataImporter(self._session).import_tasks(ctx)

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
                self.record_import_preview(
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

    def record_import_preview(
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

    def _preview_token_response(
        self,
        *,
        workspace: Workspace,
        request: WorkspaceImportRequest,
        preview_token: str,
        created_counts: dict[str, int],
        skipped_counts: dict[str, int],
        id_map: dict[str, dict[str, str]],
        conflict_plan: list[WorkspaceImportConflict],
    ) -> WorkspaceImportResponse | None:
        if request.preview_token is None or request.preview_token == preview_token:
            return None
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
        return response

    def _unsupported_format_response(
        self,
        *,
        workspace: Workspace,
        request: WorkspaceImportRequest,
        preview_token: str,
        created_counts: dict[str, int],
        skipped_counts: dict[str, int],
        id_map: dict[str, dict[str, str]],
        conflict_plan: list[WorkspaceImportConflict],
    ) -> WorkspaceImportResponse | None:
        unsupported_format = _unsupported_format_conflict(request.export)
        if unsupported_format is None:
            return None
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
        return response
