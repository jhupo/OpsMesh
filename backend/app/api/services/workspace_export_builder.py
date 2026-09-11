from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.exports import (
    WorkspaceExportManifest,
    WorkspaceExportRequest,
    WorkspaceExportResponse,
)
from backend.app.api.services.workspace_export_constants import SUPPORTED_WORKSPACE_EXPORT_FORMAT
from backend.app.api.services.workspace_export_payloads import (
    _agent_payload,
    _artifact_payload,
    _audit_payload,
    _file_payload,
    _run_event_payload,
    _run_payload,
    _runtime_space_payload,
    _runtime_space_quota_payload,
    _skill_install_payload,
    _task_message_payload,
    _task_payload,
    _task_step_payload,
    _team_member_payload,
    _team_payload,
    _workspace_payload,
)
from backend.app.files.artifact_models import Artifact
from backend.app.observability.audit_models import AuditEvent
from backend.app.observability.audit_service import AuditService
from backend.app.capabilities.models import WorkspaceSkillInstall
from backend.app.files.models import WorkspaceFile
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workspaces.models import Workspace


class WorkspaceExportBuilder:
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
