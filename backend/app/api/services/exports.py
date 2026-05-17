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
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.files.models import WorkspaceFile
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
